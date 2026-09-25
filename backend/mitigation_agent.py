import hashlib
import html as html_lib
import json
import logging
import re
import threading
import time
import types

import google.generativeai as genai
import requests

from config import UserConfig
from prompt_privacy import (
    deep_pseudonymize,
    deep_scrub_text,
    restore_aliases,
    sanitize_issue_for_prompt,
    scrub_emails,
)
from risk_components import has_dependency_signal

# The google-generativeai SDK keeps API-key state process-global
# (genai.configure); serialize init + calls to avoid cross-profile key races.
_GENAI_LOCK = threading.Lock()

# Cheap per-process LLM result cache (TTL). Serverless instances are ephemeral,
# but a warm instance serves repeat clicks (e.g. re-running "Mitigate with AI")
# from cache instead of re-hitting the (slow, quota-limited) provider.
_LLM_CACHE: dict = {}
_LLM_CACHE_TTL = 600  # seconds


def _llm_cache_key(prefix: str, obj) -> str:
    try:
        payload = json.dumps(obj, sort_keys=True, default=str)
    except Exception:
        payload = str(obj)
    digest = hashlib.md5(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _llm_cache_get(key: str):
    item = _LLM_CACHE.get(key)
    if item and (time.time() - item[1]) < _LLM_CACHE_TTL:
        return item[0]
    _LLM_CACHE.pop(key, None)
    return None


def _llm_cache_put(key: str, value) -> None:
    _LLM_CACHE[key] = (value, time.time())


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Matches Jira issue keys like MOS-21, PFIN-123
JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9_]+-\d+)\b")


class OpenRouterModel:
    """Minimal OpenAI-style wrapper around OpenRouter's chat completions endpoint."""

    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, api_key, model):
        self.model = model
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://sprint-risk-radar.vercel.app",
            "X-Title": "Agile Comrade",
        }

    def generate_content(self, prompt):
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 2000,
        }
        response = requests.post(self.API_URL, json=payload, headers=self.headers, timeout=45)
        response.raise_for_status()
        data = response.json()
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        return types.SimpleNamespace(text=content)


def filter_false_external_deps(risks, issues):
    """Drop AI EXTERNAL_DEPENDENCY findings no real ticket actually supports.

    The LLM is good at spotting explicit "blocked"/"waiting" language but prone to
    reading a descriptive mention ("export to external tools") as a real dependency.
    Every EXTERNAL_DEPENDENCY key the model returns is therefore re-checked against
    the real ticket: a `blocked_by` link or an explicit blocking phrase. Unsupported
    keys are stripped, and the whole risk is removed when none remain. Sprint-wide
    findings (empty issue_keys) are left untouched — there is nothing to verify.
    """
    by_key = {}
    for i in issues:
        k = (i.get("key") or "").strip().upper()
        if k:
            by_key[k] = i

    kept = []
    dropped = 0
    for r in risks:
        if not isinstance(r, dict) or r.get("type") != "EXTERNAL_DEPENDENCY":
            kept.append(r)
            continue
        keys = r.get("issue_keys") or []
        if not keys:
            kept.append(r)
            continue
        survivors = []
        for k in keys:
            key = str(k).strip().upper()
            issue = by_key.get(key)
            if issue is not None and has_dependency_signal(issue):
                survivors.append(k)
        if not survivors:
            dropped += 1
            continue
        r = dict(r)
        r["issue_keys"] = survivors
        r["count"] = len(survivors)
        if len(survivors) == 1:
            r["issue_key"] = survivors[0]
        kept.append(r)

    if dropped:
        logger.info(
            f"AI next-sprint | dropped {dropped} unsupported EXTERNAL_DEPENDENCY "
            f"finding(s) (no real dependency signal on the cited tickets)"
        )
    return kept


class MitigationAgent:
    """Per-profile LLM agent. Uses exactly one provider (gemini | openrouter)."""

    PROVIDERS = ("gemini", "openrouter")

    def __init__(self, config: UserConfig):
        self.config = config
        self.provider = (config.llm_provider or "gemini").lower()
        self.model = None
        self._provider_name = self.provider

        if self.provider not in self.PROVIDERS:
            self.provider = "gemini"

        api_key = config.llm_api_key
        model = config.llm_model
        self._api_key = api_key

        try:
            if self.provider == "gemini":
                if not api_key:
                    raise RuntimeError("Gemini API key is empty")
                # genai.configure mutates process-global SDK state; serialize it
                # so concurrent profiles cannot race each other's API key in.
                with _GENAI_LOCK:
                    genai.configure(api_key=api_key)
                    self.model = genai.GenerativeModel(model or "gemini-flash-latest")
            elif self.provider == "openrouter":
                if not api_key:
                    raise RuntimeError("OpenRouter API key is empty")
                self.model = OpenRouterModel(api_key, model or "openai/gpt-4o-mini")
            logger.info(f"🤖 LLM ready: {self.provider} | {model or 'default'}")
        except Exception as e:
            logger.error(f"LLM provider '{self.provider}' failed to initialize: {e}. Using rule-based fallback.")
            self.model = None
            self._provider_name = "rule-based"

    @staticmethod
    def _fallback_reason(exc: Exception) -> str:
        """Classify an LLM failure so the UI can explain why a rule-based
        plan/message was substituted (see describeAiFallback in the app)."""
        text = str(exc).lower()
        if "no llm provider" in text:
            return "not_configured"
        if "busy" in text or "in flight" in text:
            return "busy"
        if "timeout" in text or "timed out" in text or "deadline" in text:
            return "timeout"
        if any(k in text for k in ("api key", "unauthorized", "401", "403", "permission", "quota", "rate limit")):
            return "auth"
        return "provider_error"

    @staticmethod
    def _is_retryable(err: Exception) -> bool:
        s = str(err).lower()
        return any(
            t in s
            for t in (
                "429",
                "rate limit",
                "resource exhausted",
                "timeout",
                "deadlineexceeded",
                "503",
                "502",
                "500",
                "unavailable",
                "overloaded",
            )
        )

    def _generate_with_model(self, prompt):
        if not self.model:
            raise RuntimeError("No LLM provider configured")

        # Gemini free tier is prone to transient 429/timeout errors; retry a
        # few times with exponential backoff before degrading to rule-based.
        max_retries = 3
        last_err: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                if self.provider == "openrouter":
                    # Stateless per-instance HTTP client; no shared state to guard.
                    return self.model.generate_content(prompt)
                # genai.configure mutates process-global SDK state, so Gemini
                # calls must be serialized AND re-configured per call. Re-setting
                # the key here (not just at init) prevents concurrent requests
                # from different users on the same serverless instance from
                # cross-contaminating each other's API keys.
                if not _GENAI_LOCK.acquire(timeout=30):
                    raise RuntimeError("LLM busy — another analysis is in flight")
                try:
                    genai.configure(api_key=self._api_key)
                    return self.model.generate_content(
                        prompt,
                        generation_config={"max_output_tokens": 1024, "temperature": 0.3},
                        request_options={"timeout": 45},
                    )
                finally:
                    _GENAI_LOCK.release()
            except Exception as e:
                last_err = e
                if attempt == max_retries or not self._is_retryable(e):
                    break
                wait = 2 ** (attempt - 1)  # 1s, then 2s
                logger.warning(
                    f"AI call attempt {attempt} failed ({self.provider}): {e}. "
                    f"Retrying in {wait}s..."
                )
                time.sleep(wait)

        logger.error(f"LLM call failed after {max_retries} attempts: {last_err}")
        raise last_err

    # ---------------- privacy: prompt sanitization ---------------- #
    # See prompt_privacy.py — assignees become per-call pseudonyms before
    # payloads leave for the provider; emails are scrubbed from free text.

    def generate_sprint_mitigation_plan(self, sprints):
        mitigations = []
        for sprint in sprints:
            mitigations.append(self._generate_sprint_mitigation(sprint))
        return mitigations

    def _generate_sprint_mitigation(self, sprint):
        sprint_key = sprint.get("sprint_key")
        cache_key = _llm_cache_key("mit", {"s": sprint_key, "r": sprint.get("risks", []), "i": sprint.get("issues", [])})
        cached = _llm_cache_get(cache_key)
        if cached is not None:
            logger.info(f"AI mitigation | cache hit | sprint={sprint_key}")
            return cached
        project_key = sprint.get("project_key", "N/A")
        risks = sprint.get("risks", [])
        issues = sprint.get("issues", [])
        prompt, prompt_mapping = self._build_sprint_prompt(sprint)

        try:
            response = self._generate_with_model(prompt)
            # The LLM only ever sees pseudonyms; map them back to real names
            # before display/extraction. raw_response keeps the alias-only text.
            mitigation_text = restore_aliases(response.text, prompt_mapping)

            mitigation = {
                "sprint_key": sprint_key,
                "project_key": project_key,
                "risk_count": len(risks),
                "risk_types": sorted(set(r.get("type") for r in risks)),
                "risk_score": max([r.get("risk_score", 0) for r in risks], default=0),
                "burndown_gap_percent": max(
                    [r.get("burndown_gap_percent", 0) for r in risks if r.get("type") == "BURNDOWN_BEHIND"],
                    default=None,
                ),
                "confidence": self._extract_confidence(mitigation_text) or max([r.get("confidence", 0) for r in risks], default=0),
                "ai_mitigation_suggestion": mitigation_text,
                "prompt": prompt,
                "raw_response": response.text,
                "ai_used": True,
                "llm": self.get_model_info(),
                "action_items": self._extract_action_items(mitigation_text),
                "owner": self._extract_owner(mitigation_text),
                "timeline": self._extract_timeline(mitigation_text),
                "success_criteria": self._extract_bullets(self._extract_section(mitigation_text, "SUCCESS CRITERIA:")),
                "details": [{
                    "key": issue.get("key"),
                    "summary": issue.get("summary"),
                    "status": issue.get("status"),
                    "story_points": issue.get("story_points", 0),
                    "assignee": issue.get("assignee", "Unassigned"),
                    "due_date": issue.get("due_date"),
                    "risk_type": self._risk_type_for_issue(risks, issue.get("key")),
                } for issue in issues],
            }

            logger.info(
                f"AI mitigation | source=LLM | provider={self.provider} | sprint={sprint_key}"
            )
            _llm_cache_put(cache_key, mitigation)
            return mitigation

        except Exception as e:
            logger.error(
                f"AI mitigation | source=rule-based | provider={self.provider} | sprint={sprint_key} | error={e}"
            )
            if not risks:
                owner = "Scrum Master — no risks detected, Please proactively keep assessing."
                timeline = "Keep checking (reassess at the next standup)"
            else:
                owner = self._fallback_owner(risks) or "Scrum Master"
                timeline = "ASAP (within 24 hours)"
            return {
                "sprint_key": sprint_key,
                "project_key": project_key,
                "risk_count": len(risks),
                "risk_types": sorted(set(r.get("type") for r in risks)),
                "risk_score": max([r.get("risk_score", 0) for r in risks], default=0),
                "confidence": max([r.get("confidence", 0) for r in risks], default=0),
                "ai_mitigation_suggestion": self._sprint_fallback_suggestion(risks),
                "prompt": prompt,
                "raw_response": "",
                "ai_used": False,
                "fallback_reason": self._fallback_reason(e),
                "llm": self.get_model_info(),
                "action_items": [],
                "owner": owner,
                "timeline": timeline,
                "success_criteria": [],
                "details": [{
                    "key": issue.get("key"),
                    "summary": issue.get("summary"),
                    "status": issue.get("status"),
                    "story_points": issue.get("story_points", 0),
                    "assignee": issue.get("assignee", "Unassigned"),
                    "due_date": issue.get("due_date"),
                    "risk_type": self._risk_type_for_issue(risks, issue.get("key")),
                } for issue in issues],
                "error": str(e),
            }

    def _sprint_fallback_suggestion(self, risks):
        if not risks:
            return "Sprint is on track. No mitigation needed at this time."
        parts = []
        for r in risks[:5]:
            parts.append(f"{r.get('type')}: {r.get('recommendation', '')}")
        return " | ".join(parts)

    def _fallback_owner(self, risks):
        """Compose a Scrum-Master-coordination owner message for the rule-based
        fallback, naming each risk's cause and the recovery lever (built from
        the diagnostic fields already on each risk object). Merge the top two
        risks by risk_score; assignee names are intentionally not used."""
        if not risks:
            return ""
        def phrase(r):
            rtype = r.get("type")
            if rtype == "BURNDOWN_BEHIND":
                gap = r.get("burndown_gap_percent")
                sp = r.get("remaining_sp")
                count = len(r.get("issue_keys") or [])
                gap_txt = f"{gap:.1f}%" if gap is not None else "the gap"
                sp_txt = f"{sp:.0f} SP" if sp is not None else "work"
                count_txt = f"{count} open items" if count else "open items"
                return (
                    f"Scrum Master — burndown is {gap_txt} behind: {sp_txt} / "
                    f"{count_txt} still incomplete. Re-plan capacity and "
                    f"reprioritize remaining work to get back on track."
                )
            if rtype == "QA_BOTTLENECK":
                n = r.get("qa_stories_count") or 0
                stuck = len(r.get("stuck_stories") or [])
                stuck_txt = f" ({stuck} stuck >24h)" if stuck else ""
                return (
                    f"Scrum Master — {n} stories are in QA review{stuck_txt}. "
                    f"Balance QA load or add review capacity to clear the queue."
                )
            if rtype == "BUG_RAISED":
                key = r.get("issue_key") or "a bug"
                tier = r.get("tier") or ""
                tier_txt = f" ({tier})" if tier else ""
                return (
                    f"Scrum Master — coordinate the fix for {key}{tier_txt} so it's "
                    f"addressed before sprint end and doesn't break DoD."
                )
            if rtype == "SCOPE_CREEP":
                growth = r.get("growth_percent")
                growth_txt = f"{growth:.0f}%" if growth is not None else "beyond plan"
                baseline = r.get("baseline_sp")
                current = r.get("current_sp")
                if baseline is not None and current is not None:
                    delta_txt = f"{baseline:.0f} → {current:.0f} SP"
                else:
                    delta_txt = "plan"
                return (
                    f"Scrum Master — scope grew {growth_txt} vs planning "
                    f"({delta_txt}). Renegotiate scope with stakeholders rather "
                    f"than absorbing extra work."
                )
            if rtype == "SPRINT_ENDED_INCOMPLETE":
                return (
                    "Scrum Master — sprint ended with work incomplete. Recover "
                    "remaining scope or close out with a clear plan."
                )
            if rtype == "SPRINT_NOT_STARTED":
                return (
                    "Scrum Master — sprint hasn't started. Resolve blockers and "
                    "kick off to protect the sprint goal."
                )
            return None

        ordered = sorted(risks, key=lambda r: r.get("risk_score", 0), reverse=True)
        parts = [p for r in ordered[:2] if (p := phrase(r))]
        return " | ".join(parts) if parts else ""


    def _risk_type_for_issue(self, risks, issue_key):
        return next(
            (r.get("type") for r in risks if r.get("issue_key") == issue_key),
            "N/A",
        )

    def _build_sprint_prompt(self, sprint):
        sprint_key = sprint.get("sprint_key")
        project_key = sprint.get("project_key", "N/A")
        risks = sprint.get("risks", [])
        issues = sprint.get("issues", [])

        # Cap the ticket payload and trim free text to keep the prompt (and the
        # provider's generation time) small — large sprints were a major source
        # of slow / 504 responses.
        sprint_issues = [{
            "key": i.get("key"),
            "summary": (i.get("summary") or "")[:300],
            "status": i.get("status"),
            "story_points": i.get("story_points", 0),
            "assignee": i.get("assignee", "Unassigned"),
            "due_date": i.get("due_date"),
            "description": re.sub(r"\s+", " ", i.get("description") or "")[:250],
            "acceptance_criteria": re.sub(r"\s+", " ", i.get("acceptance_criteria") or "")[:250],
        } for i in issues[:30]]
        prompt_mapping = {}
        sprint_issues = [sanitize_issue_for_prompt(i, prompt_mapping) for i in sprint_issues]
        # Risks carry assignee keys AND free-text recommendations that can embed
        # real names ("Check with John..."). Pseudonymize keys first (fills the
        # shared map), then scrub every string value.
        risks_for_prompt = deep_scrub_text(deep_pseudonymize(risks, prompt_mapping), prompt_mapping)

        prompt = f"""
You are an expert Scrum Master/Project Manager. Analyze this sprint and its risks, then provide actionable mitigation strategies at the sprint level.

Sprint: {sprint_key}
Project: {project_key}
Number of Risks: {len(risks)}

Risks:
{json.dumps(risks_for_prompt, indent=2, default=str)}

Sprint Tickets:
{json.dumps(sprint_issues, indent=2, default=str)}

Each ticket includes a "due_date" (YYYY-MM-DD, null if unset). Treat a ticket whose due_date is today or past and not Done as DUE_DATE_PASSED, and prioritize mitigation of those tickets.

Provide mitigation suggestions using EXACTLY these section headers, one per line, in this order:

ACTION ITEMS:
- <specific, actionable step 1>
- <specific, actionable step 2>
- <specific, actionable step 3>

OWNER:
- <Role or Person>: <what they are responsible for>

TIMELINE:
- <when>: <what happens by then>

CONFIDENCE:
<70-100>%

SUCCESS CRITERIA:
- <how to measure success 1>
- <how to measure success 2>

Rules:
- Use the exact section headers shown above (ALL CAPS, ending with a colon). Do not number them, do not add sub-headings, do not use "###", "####", or "**" formatting.
- Each bullet line must start with "- ".
- Keep the plan concise and practical for a team standup.
- Every section must be present, even if brief.
"""
        return prompt, prompt_mapping

    def _extract_section(self, text, header):
        lines = text.split("\n")
        section_lines = []
        in_section = False
        for line in lines:
            stripped = line.strip()
            if in_section:
                if stripped and stripped.isupper() and stripped.endswith(":"):
                    break
                if stripped and not stripped.startswith("-") and not stripped.startswith("*") and not stripped.startswith("•"):
                    if any(h in stripped.upper() for h in ["ACTION ITEMS", "OWNER:", "TIMELINE:", "CONFIDENCE:", "SUCCESS CRITERIA"]):
                        break
                if stripped:
                    section_lines.append(stripped)
            elif stripped.upper().startswith(header):
                in_section = True
        return section_lines

    def _extract_bullets(self, section_lines):
        items = []
        for line in section_lines:
            for prefix in ["- ", "* ", "• "]:
                if line.startswith(prefix):
                    line = line[len(prefix):].strip()
                    break
            if line:
                items.append(line)
        return items

    def _extract_action_items(self, text):
        return self._extract_bullets(self._extract_section(text, "ACTION ITEMS:"))[:5]

    def _extract_owner(self, text):
        owners = self._extract_bullets(self._extract_section(text, "OWNER:"))
        return "; ".join(owners) if owners else "Scrum Master (escalate if needed)"

    def _extract_timeline(self, text):
        items = self._extract_bullets(self._extract_section(text, "TIMELINE:"))
        return "; ".join(items) if items else "ASAP (within 24 hours)"

    def _extract_confidence(self, text):
        section = self._extract_section(text, "CONFIDENCE:")
        for line in section:
            nums = [int(s) for s in line.split() if s.isdigit()]
            if nums:
                return max(nums)
        return None

    def generate_followup_message(self, blocker):
        cache_key = _llm_cache_key("followup", blocker)
        cached = _llm_cache_get(cache_key)
        if cached is not None:
            logger.info(f"AI follow-up | cache hit | issue={blocker.get('issue_key')}")
            return cached
        prompt = self._build_followup_prompt(blocker)
        real_assignee = (blocker.get("assignee") or "").strip()
        alias = "dev-01" if real_assignee else None

        try:
            response = self._generate_with_model(prompt)
            text = response.text.strip()
            if alias:
                # Restore the real name locally; the pseudonym never ships.
                text = restore_aliases(text, {real_assignee: alias})
            message = self._linkify_issue_keys(text)
            logger.info(f"AI follow-up | source=LLM | provider={self.provider}")
            result = {
                "message": message,
                "generated_by": "ai",
                "raw": text,
            }
            _llm_cache_put(cache_key, result)
            return result
        except Exception as e:
            logger.error(f"AI follow-up | source=rule-based | provider={self.provider} | error={e}")
            return {
                "message": self._linkify_issue_keys(self._rule_based_followup_message(blocker)),
                "generated_by": "rule-based",
                "fallback_reason": self._fallback_reason(e),
                "error": str(e),
            }

    def _linkify_issue_keys(self, text):
        escaped = html_lib.escape(text or "")

        def replace(match):
            key = match.group(0)
            url = f"{self.config.jira_cloud_url.rstrip('/')}/browse/{key}"
            return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{key}</a>'

        return JIRA_KEY_RE.sub(replace, escaped)

    def _build_followup_prompt(self, blocker):
        issue_key = blocker.get("issue_key")
        summary = scrub_emails(blocker.get("summary")) or "this ticket"
        real_assignee = (blocker.get("assignee") or "").strip()
        # Pseudonymize the person's name; the caller restores it in the
        # generated text so it never leaves this server.
        assignee = "dev-01" if real_assignee else "the assignee"
        risk_type = blocker.get("type", "RISK")
        hours = blocker.get("hours_since_update")
        status = blocker.get("status")
        sprint_key = blocker.get("sprint_key", "the current sprint")

        context_lines = [
            f"- Ticket: {issue_key}",
            f"- Summary: {summary}",
            f"- Assignee: {assignee}",
            f"- Risk type: {risk_type}",
            f"- Sprint: {sprint_key}",
        ]
        if hours is not None:
            context_lines.append(f"- Hours since last update: {hours}")
        if status:
            context_lines.append(f"- Current status: {status}")

        return f"""
You are an experienced Scrum Master writing a polite, professional follow-up message about a blocked ticket.

TICKET CONTEXT:
{chr(10).join(context_lines)}

Write a short, friendly follow-up message (2-4 sentences) to {assignee} asking for an update on {issue_key}.
- Be concise and actionable, suitable for Slack/Teams or email.
- Reference the specific blocker/risk ({risk_type}).
- If the ticket has been idle for many hours, gently flag that it needs attention.
- End with a clear request (e.g. share an update, unblock, or pair).
- IMPORTANT: Use PLAIN TEXT ONLY. Do NOT use any markdown, asterisks, bold, italics, or emoji formatting.
- Refer to the ticket using its key (e.g. {issue_key}) without any special characters around it.
- Do not mention that this was AI-generated. Return only the message text.
"""

    def _rule_based_followup_message(self, blocker):
        issue_key = blocker.get("issue_key") or "this ticket"
        summary = blocker.get("summary")
        assignee = blocker.get("assignee") or "team"
        risk_type = blocker.get("type", "risk")
        hours = blocker.get("hours_since_update")

        msg = f"Hi {assignee}, quick check-in on {issue_key}"
        if summary:
            msg += f" ({summary})"
        msg += "."
        if hours is not None:
            msg += f" It has been {hours} hours without an update."
        msg += f" Could you share the current status and any blockers ({risk_type})? Thanks!"
        return msg

    def get_model_info(self):
        if self.provider == "openrouter":
            return {"provider": "openrouter", "model": self.config.llm_model or "openai/gpt-4o-mini", "base_url": "https://openrouter.ai"}
        return {"provider": "gemini", "model": self.config.llm_model or "gemini-flash-latest", "base_url": None}

    def analyze_next_sprint_risks(self, project_key, sprint, issues, rule_based_risks=None):
        rule_based_risks = rule_based_risks or []
        cache_key = _llm_cache_key("next", {"p": project_key, "i": issues})
        cached = _llm_cache_get(cache_key)
        if cached is not None:
            logger.info(f"AI next-sprint | cache hit | project={project_key}")
            return cached
        prompt, prompt_mapping = self._build_next_sprint_risk_prompt(project_key, sprint, issues)

        try:
            response = self._generate_with_model(prompt)
            text = restore_aliases(response.text.strip(), prompt_mapping)
            raw_risks = self._extract_risk_list(text)
            # Re-check every EXTERNAL_DEPENDENCY against the real tickets so the
            # LLM can't turn a descriptive "external" mention into a blocker.
            raw_risks = filter_false_external_deps(raw_risks, issues)

            info = self.get_model_info()
            if raw_risks:
                logger.info(f"🤖 AI identified {len(raw_risks)} early risks for {project_key} next sprint "
                               f"[{info['provider']} | {info['model']}]")
                logger.info(f"AI next-sprint | source=LLM | provider={info['provider']} | project={project_key}")
                result = (raw_risks, True, prompt, text, None)
                _llm_cache_put(cache_key, result)
                return result
            logger.warning(f"⚠️ AI returned empty risk list for {project_key}. Using rule-based fallback. "
                                   f"[{info['provider']} | {info['model']}]")
            return rule_based_risks, False, prompt, text, None
        except Exception as e:
            info = self.get_model_info()
            logger.error(f"AI next-sprint | source=rule-based | provider={info['provider']} | project={project_key} | error={e}")
            return rule_based_risks, False, prompt, "", str(e)

    def _build_next_sprint_risk_prompt(self, project_key, sprint, issues):
        sprint_key = sprint.get("name") or sprint.get("id")
        start_date = sprint.get("startDate")
        end_date = sprint.get("endDate")

        issue_list = [{
            "key": i.get("key"),
            "summary": (i.get("summary") or "")[:300],
            "status": i.get("status"),
            "story_points": i.get("story_points", 0),
            "assignee": i.get("assignee", "Unassigned"),
            "issue_type": i.get("issue_type"),
            "priority": i.get("priority"),
            "description": re.sub(r"\s+", " ", i.get("description") or "")[:250],
            "acceptance_criteria": re.sub(r"\s+", " ", i.get("acceptance_criteria") or "")[:250],
            "due_date": i.get("due_date"),
        } for i in issues[:30]]
        prompt_mapping = {}
        issue_list = [sanitize_issue_for_prompt(i, prompt_mapping) for i in issue_list]
        issue_list = [deep_scrub_text(i, prompt_mapping) for i in issue_list]

        prompt = f"""
You are an expert Scrum Master performing pre-planning risk analysis on an UPCOMING sprint.

Sprint: {sprint_key}
Project: {project_key}
Planned Dates: {start_date} → {end_date}
Number of Work Items: {len(issues)}

Planned Work Items:
{json.dumps(issue_list, indent=2, default=str)}

Analyze the upcoming sprint for early risks BEFORE planning begins. Consider:
- Unassigned work items (capacity / ownership gaps)
- Missing story points (sizing / velocity concerns)
- Undefined scope (missing or thin acceptance_criteria; no description)
- Acceptance criteria too thin for the estimated size (e.g. 8+ SP with a single short scenario)
- External dependencies: ONLY flag EXTERNAL_DEPENDENCY when an item is genuinely blocked on or waiting for another party (e.g. "waiting for the vendor", "blocked by the platform team", "depends on their API"). Do NOT flag descriptive mentions of tools/systems the item merely integrates with, exports to, or mentions (e.g. "export to external tools", "integrate with a vendor API", "external archive") unless the item is actually blocked on that party.
- Assignee overload (one person carrying too many items)
- Priority conflicts or high-priority items with large estimates
- Too much work vs. team capacity (velocity)
- Due-date checkpoint: only flag DUE_DATE_PASSED when a work item HAS a due_date that is on or before the sprint start date, or already in the past. A null/missing due_date is NOT a risk — but note missing due dates as a planning checkpoint (confirm due dates during planning) rather than a scored risk

Each work item includes "description", "acceptance_criteria", and "due_date" (YYYY-MM-DD, null if unset) fields.
Judge scope completeness from "acceptance_criteria", not just the description text.
Treat due_date as a pre-planning checkpoint: flag items with an EXPLICIT past/at-start due_date as "DUE_DATE_PASSED". Never emit DUE_DATE_PASSED for items with a null due_date.

Return your findings as a valid JSON array. Each element MUST be an object with exactly these keys:
  "type"         - short ALL-CAPS identifier (e.g. "UNASSIGNED", "UNESTIMATED", "OVERLOADED", "EXTERNAL_DEPENDENCY", "SCOPE_RISK", "DUE_DATE_PASSED")
  "issue_keys"   - list of affected ticket keys (strings); use [] if sprint-wide
  "count"        - number of affected tickets
  "risk_score"   - integer 0-100
  "confidence"   - integer 0-100
  "severity"     - "CRITICAL", "HIGH", "MEDIUM", or "LOW"
  "recommendation" - one concise actionable sentence to de-risk before planning

Rules:
- Return ONLY the JSON array. No markdown fences, no code block markers, no extra text.
- If no meaningful risks exist, return an empty array: []
- Maximum 6 risks, ranked by risk_score descending.
"""
        return prompt, prompt_mapping

    def _extract_risk_list(self, text):
        text = text.strip()
        fence_match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()
        try:
            return json.loads(text)
        except Exception:
            start = text.find("[")
            end = text.rfind("]")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except Exception:
                    return []
            return []

    def generate_stakeholder_report(self, risks, mitigations, sprint_data=None):
        report_mapping = {}
        prompt = f"""
You are creating a Sprint Status Report for Product Owners and Executives.

Total Risks Identified: {len(risks)}
Critical Severity Risks: {len([r for r in risks if r.get('severity') == 'CRITICAL'])}
High Severity Risks: {len([r for r in risks if r.get('severity') == 'HIGH'])}
Medium Severity Risks: {len([r for r in risks if r.get('severity') == 'MEDIUM'])}

Risks Summary:
{json.dumps(deep_pseudonymize(risks[:5], report_mapping), indent=2, default=str)}

Mitigations Proposed:
{json.dumps(deep_pseudonymize(mitigations[:5], report_mapping), indent=2, default=str)}

Generate a 3-paragraph executive summary:
1. Current Sprint Status (one sentence)
2. Key Risks & Impact (2-3 risks)
3. Mitigation Plan & Next Steps

Make it suitable for a 5-minute stakeholder update. Be direct and actionable.
"""

        try:
            response = self._generate_with_model(prompt)
            logger.info(f"AI report | source=LLM | provider={self.provider}")
            return restore_aliases(response.text, report_mapping)
        except Exception as e:
            logger.error(f"AI report | source=rule-based | provider={self.provider} | error={e}")
            return f"Unable to generate report. {len(risks)} risks detected, manual review required."
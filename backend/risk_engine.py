"""v2 risk engine implementing the Sprint Risk Scoring Rubric v2.

All triggers remain the same as v1; only scoring changed. Each risk emits a
capped `risk_score` (for UI/severity) and an uncapped `raw_score` (for
sprint-to-sprint and ticket-to-ticket ranking/triage).
"""
import logging
from datetime import date, timezone
from zoneinfo import ZoneInfo

from config import settings


def _resolve_tz(jira_timezone: str | None):
    """Return a tzinfo from an IANA timezone name (the Jira user's timezone),
    falling back to UTC when none is provided or unparseable. This keeps the
    backend's calendar-day math aligned with what Jira displays regardless of
    where the server runs (e.g. Vercel = UTC)."""
    if jira_timezone:
        try:
            return ZoneInfo(jira_timezone)
        except Exception:  # noqa: BLE001 - unknown tz string
            pass
    return timezone.utc
from risk_components import (
    STALE_HOURS,
    assignee_factor,
    avg_sprint_sp,
    bucket_severity,
    cap_score,
    days_remaining,
    hours_since,
    is_blocking_map,
    is_done,
    is_qa_status,
    now_utc,
    size_weight,
    time_pressure_multiplier,
    to_utc,
    trend_factor,
    workflow_stage_weight,
)
from risk_matrix import (
    burndown_i,
    burndown_p,
    qa_i,
    qa_p,
    scope_creep_i,
    scope_creep_p,
    score_from_matrix,
    sprint_ended_i,
    sprint_ended_p,
    sprint_not_started_i,
    sprint_not_started_p,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EXTERNAL_KEYWORDS = ["vendor", "third-party", "third party", "procurement", "external", "credentials"]
INTERNAL_KEYWORDS = ["another team", "other team", "internal", "platform team", "another squad", "squad"]
TRIGGER_KEYWORDS = ["blocked by", "depends on", "waiting for", "external", "vendor", "third-party"]

DEPENDENCY_BASE = {
    "external": settings.dependency_external_base,
    "internal": settings.dependency_internal_base,
    "default": settings.dependency_default_base,
}

# Risk scoring runs through two tails that share `_emit`:
#
#   5x5 matrix (sprint-level detectors, see risk_matrix.py):
#       matrix_value = P × I                       # 1..25
#       risk_score = project_matrix(matrix_value)   # band-aligned 0..100
#       severity = matrix band of (P × I)
#
#   legacy product (ticket-level detectors, being migrated in phase 2):
#       raw_score = base × ∏(named multipliers)     # uncapped, for triage
#       risk_score = min(100, round(raw_score))
#       severity = bucket_severity(risk_score)
#
# RISK_RECIPES is the single source of truth for WHICH multipliers apply to
# WHICH risk type in the legacy product tail. A detector is built by the
# generic `_apply_recipe`, which multiplies in each named factor (missing
# entries default to a neutral 1.0). Risk types that do not use the multiplier
# model (BUG_RAISED band model, DUE_DATE's max-aggregate) list an empty recipe.
RISK_RECIPES = {
    "STORY_NOT_PROGRESSING": ("stage", "assignee", "size"),
    "SPRINT_NOT_STARTED": ("pressure",),
    "BURNDOWN_BEHIND": ("trend", "pressure"),
    "QA_BOTTLENECK": ("pressure",),
    "EXTERNAL_DEPENDENCY": ("fan_out", "size"),
    "DUE_DATE_PASSED": ("stage", "size", "blocking"),  # per-ticket; the sprint risk maxes them
    "BUG_RAISED": (),  # band model - the base IS the score
    "SCOPE_CREEP": ("pressure",),  # + score_floor applied by the detector
    "SPRINT_ENDED_INCOMPLETE": (),  # additive formula - the base IS the score
}


class RiskEngine:

    # ------------------------------------------------------------------ #
    # Orchestrator
    # ------------------------------------------------------------------ #
    def calculate_all_risks(self, sprint_data, velocity_data=None, burndown_history=None, scope_meta=None, jira_timezone=None):
        risks = []
        burndown_history = burndown_history or {}
        scope_meta = scope_meta or {}
        baselines = scope_meta.get("baselines") or {}
        scope_history = scope_meta.get("history") or {}

        for project_key, data in sprint_data.items():
            sprint = data["sprint"]
            issues = data["issues"]

            context = {
                "avg_sp": avg_sprint_sp(issues),
                "blocking_map": is_blocking_map(issues),
                "qa_throughput": self._qa_throughput(velocity_data, project_key),
                "burndown_history": burndown_history.get(sprint.get("name")) if sprint else None,
                "scope_baseline": baselines.get(sprint.get("name")) if sprint else None,
                "scope_history": scope_history.get(sprint.get("name")) if sprint else None,
            }

            # Run each detector in isolation: a bug in one must not blank the
            # entire panel. Failure is logged and skipped, the rest still run.
            detectors = [
                ("story_progress", lambda: self.detect_story_progress_risks(sprint, issues, context)),
                ("no_progress", lambda: self.detect_sprint_no_progress(sprint, issues, context)),
                ("burndown", lambda: self.detect_burndown_risks(sprint, issues, context)),
                ("qa_bottleneck", lambda: self.detect_qa_bottleneck(sprint, issues, context)),
                ("external_dependencies", lambda: self.detect_external_dependencies(issues, context)),
                ("due_date", lambda: self.detect_due_date_risks(sprint, issues, context, jira_timezone)),
                ("bug", lambda: self.detect_bug_risks(sprint, issues, context)),
                ("scope_creep", lambda: self.detect_scope_creep(sprint, issues, context)),
                ("sprint_overdue", lambda: self.detect_sprint_overdue_risk(sprint, issues, context, jira_timezone)),
            ]
            sprint_name = sprint.get("name") if sprint else project_key
            for label, fn in detectors:
                try:
                    found = fn()
                    if found:
                        risks.extend(found)
                except Exception as e:  # noqa: BLE001 - isolate detector failures
                    logger.warning(f"Risk detector '{label}' failed for {sprint_name}: {e}")
                    continue

        return sorted(risks, key=lambda x: x["raw_score"], reverse=True)

    @staticmethod
    def _qa_throughput(velocity_data, project_key):
        from risk_components import qa_throughput_per_day
        return qa_throughput_per_day(velocity_data, project_key)

    # ------------------------------------------------------------------ #
    # Generic scoring helpers (shared by every detector)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _apply_recipe(base, recipe, values):
        """Multiply `base` by each named multiplier in `recipe`.

        Each name is looked up in `values` (a dict the detector builds with the
        exact factors it computed); a missing key defaults to a neutral 1.0 so a
        detector can legitimately omit factors that don't apply to a given ticket.
        Returns the uncapped `base` product — the shared scoring tail (_emit)
        caps and buckets it.
        """
        for name in recipe:
            factor = values.get(name, 1.0)
            base *= factor if factor is not None else 1.0
        return base

    def _emit(self, risk_type, raw, confidence, recommendation, score_floor=None,
              probability=None, impact=None, **extras):
        """Build the common risk payload for any detector.

        Two scoring tails share this one builder:
          - 5x5 matrix (probability/impact supplied): score from the P x I block.
          - legacy product (otherwise): score from the uncapped `raw` product.

        Both cap/bucket exactly once here, and `score_floor` is re-bucketed
        after it is applied so a floor can only ever raise severity. Detectors
        only supply their score inputs, confidence, recommendation, and
        type-specific diagnostics.
        """
        matrix = probability is not None and impact is not None
        if matrix:
            block = score_from_matrix(probability, impact)
            score = block["risk_score"]
            raw = block["raw_score"]
            severity = block["severity"]
        else:
            score = cap_score(raw)
            severity = bucket_severity(score)
        if score_floor is not None:
            score = max(score, score_floor)
            severity = bucket_severity(score)
        risk = {
            "type": risk_type,
            "risk_score": score,
            "raw_score": round(raw, 1),
            "confidence": confidence,
            "severity": severity,
            "recommendation": recommendation,
        }
        if matrix:
            risk["probability"] = block["probability"]
            risk["impact"] = block["impact"]
            risk["matrix_value"] = block["matrix_value"]
        risk.update(extras)
        return risk

    # ------------------------------------------------------------------ #
    # 1. STORY_NOT_PROGRESSING (ticket-level)
    # ------------------------------------------------------------------ #
    def detect_story_progress_risks(self, sprint, issues, context=None):
        """Flag tickets that have gone quiet DURING the current sprint.

        Staleness is measured from max(last update, sprint start): silence
        from before the sprint began (backlog-pulled tickets) does not count
        toward "not progressing" — only in-sprint inactivity does.
        """
        risks = []
        context = context or {}
        avg_sp = context.get("avg_sp", 0.0)
        sprint_start = to_utc(sprint.get("startDate")) if sprint else None

        for issue in issues:
            key = issue.get("key")
            status = issue.get("status")
            if not key or is_done(status):
                continue
            updated = to_utc(issue.get("updated"))
            if not updated:
                continue
            ref = max(updated, sprint_start) if sprint_start else updated
            h = (now_utc() - ref).total_seconds() / 3600
            if h <= STALE_HOURS:
                continue

            raw, detail = self._stalled_ticket_score(h, issue, issues, avg_sp)
            risks.append(self._emit(
                "STORY_NOT_PROGRESSING",
                raw, 85,
                (
                    f"Story '{key}' has no updates in {int(h)}h. "
                    f"Check with {issue.get('assignee')} for blockers."
                ),
                issue_key=key,
                summary=issue.get("summary"),
                assignee=issue.get("assignee"),
                status=status,
                hours_since_update=round(h, 1),
                **detail,
            ))

        return risks

    # ------------------------------------------------------------------ #
    # 1b. SPRINT_NOT_STARTED (sprint-level)
    # ------------------------------------------------------------------ #
    def detect_sprint_no_progress(self, sprint, issues, context=None):
        """Flag a sprint that is active but has 0 tickets In Progress.

        Complements STORY_NOT_PROGRESSING (which only catches per-ticket
        silence): this judges the *progress state* of the whole sprint,
        independent of each ticket's last-update time, and is not gated by
        the burndown grace period. A sprint whose every open ticket is still
        in a "not started" column (To Do / Backlog / Open / ...) after a
        short grace window is treated as a risk.
        """
        if not sprint:
            return []
        start = to_utc(sprint.get("startDate"))
        end = to_utc(sprint.get("endDate"))
        now = now_utc()
        if not start or not end or now < start or now > end:
            return []  # only judge sprints that are currently active

        days_elapsed = (now - start).days
        if days_elapsed < settings.no_progress_grace_days:
            return []
        duration = (end - start).days
        elapsed_fraction = (days_elapsed / duration) if duration > 0 else 1.0

        open_issues = [i for i in issues if not is_done(i.get("status"))]
        if not open_issues:
            return []

        NOT_STARTED = {"to do", "todo", "backlog", "open", "selected for development"}
        started = [
            i for i in open_issues
            if (i.get("status") or "").strip().lower() not in NOT_STARTED
        ]
        if started:
            return []  # at least one ticket has moved out of the start column

        probability = sprint_not_started_p(elapsed_fraction)
        impact = sprint_not_started_i(len(open_issues))

        return [self._emit(
            "SPRINT_NOT_STARTED",
            None, 90,
            (
                f"Sprint '{sprint.get('name')}' started {days_elapsed}d ago but "
                f"0 of {len(open_issues)} tickets are In Progress (all To Do). "
                f"Confirm work has kicked off or re-plan scope."
            ),
            probability=probability,
            impact=impact,
            sprint_key=sprint.get("name"),
            summary=sprint.get("name"),
            days_elapsed=days_elapsed,
            open_count=len(open_issues),
        )]

    def _stalled_ticket_score(self, h, issue, issues, avg_sp):
        """Per-ticket stalled formula (used by STORY_NOT_PROGRESSING).

        `h` is the sprint-clamped hours-since-last-update computed by the
        detector so pre-sprint silence never inflates the score.
        """
        base = min(settings.stalled_base_cap, h / 2.0)
        factors = {
            "stage": workflow_stage_weight(issue.get("status")),
            "assignee": assignee_factor(issues, issue.get("assignee")),
            "size": size_weight(issue.get("story_points") or 0, avg_sp),
        }
        raw = self._apply_recipe(base, RISK_RECIPES["STORY_NOT_PROGRESSING"], factors)
        return raw, {
            "stage_weight": factors["stage"],
            "assignee_factor": factors["assignee"],
            "size_weight": round(factors["size"], 3),
        }

    # ------------------------------------------------------------------ #
    # 2. BURNDOWN_BEHIND (sprint-level)
    # ------------------------------------------------------------------ #
    def get_burndown_gap(self, sprint, issues):
        """Return computed burndown metrics for a sprint (None if not judgeable).

        Used by main.py to persist per-check-in gap history for the trend factor.
        """
        return self._compute_burndown(sprint, issues)

    def _compute_burndown(self, sprint, issues):
        if not sprint:
            return None

        start_date = to_utc(sprint.get("startDate"))
        end_date = to_utc(sprint.get("endDate"))
        now = now_utc()
        if not start_date or not end_date:
            return None

        sprint_duration = (end_date - start_date).days
        days_elapsed = (now - start_date).days
        days_left = (end_date - now).days

        if days_elapsed <= 0 or sprint_duration <= 0:
            return None

        # Grace period: don't judge burndown until the sprint has progressed
        if days_elapsed / sprint_duration < settings.burndown_grace_period_fraction:
            return None

        total_sp = sum(issue.get("story_points", 0) for issue in issues)
        completed_sp = sum(
            issue.get("story_points", 0) * settings.status_completion_weights.get(issue.get("status"), 0)
            for issue in issues
        )
        done_sp = sum(
            issue.get("story_points", 0)
            for issue in issues
            if is_done(issue.get("status"))
        )

        expected_completion_rate = min(1.0, days_elapsed / sprint_duration)
        actual_completion_rate = completed_sp / total_sp if total_sp > 0 else 0
        burndown_gap = (expected_completion_rate - actual_completion_rate) * 100

        return {
            "sprint_duration": sprint_duration,
            "days_elapsed": days_elapsed,
            "days_remaining": days_left,
            "total_sp": total_sp,
            "completed_sp": round(done_sp, 1),
            "weighted_completed_sp": round(completed_sp, 1),
            "remaining_sp": total_sp - completed_sp,
            "expected_completion_rate": round(expected_completion_rate, 4),
            "actual_completion_rate": round(actual_completion_rate, 4),
            "burndown_gap_percent": round(burndown_gap, 1),
        }

    def detect_burndown_risks(self, sprint, issues, context=None):
        risks = []
        context = context or {}
        data = self._compute_burndown(sprint, issues)
        if not data:
            return risks

        burndown_gap = data["burndown_gap_percent"]
        if burndown_gap <= settings.burndown_behind_threshold:
            return risks

        # v3 5x5 matrix: probability = can the gap still close in time (nudged by
        # the gap trend); impact = how far behind the ideal burn line we are.
        days_left_fraction = (
            data["days_remaining"] / data["sprint_duration"]
            if data.get("sprint_duration") else 0.0
        )
        probability = burndown_p(
            days_left_fraction,
            trend_factor(context.get("burndown_history") or []),
        )
        impact = burndown_i(burndown_gap)

        risks.append(self._emit(
            "BURNDOWN_BEHIND",
            None, 90,
            (
                f"Burndown {burndown_gap:.1f}% behind. Need to complete "
                f"{data['remaining_sp']:.0f} SP in {data['days_remaining']} days."
            ),
            probability=probability,
            impact=impact,
            sprint_key=sprint.get("name"),
            issue_keys=[i.get("key") for i in issues if not is_done(i.get("status"))],
            total_sp=data["total_sp"],
            completed_sp=data["completed_sp"],
            weighted_completed_sp=data["weighted_completed_sp"],
            remaining_sp=data["remaining_sp"],
            days_remaining=data["days_remaining"],
            burndown_gap_percent=burndown_gap,
        ))

        return risks

    # ------------------------------------------------------------------ #
    # 3. QA_BOTTLENECK (sprint-level, capacity vs time)
    # ------------------------------------------------------------------ #
    def detect_qa_bottleneck(self, sprint, issues, context=None):
        risks = []
        context = context or {}

        qa_stories = [issue for issue in issues if is_qa_status(issue.get("status"))]

        if len(qa_stories) < settings.qa_bottleneck_threshold:
            return risks

        stuck_stories = []
        for issue in qa_stories:
            h = hours_since(issue.get("updated"))
            if h is not None and h > 24:
                stuck_stories.append({
                    "key": issue["key"],
                    "summary": issue.get("summary"),
                    "hours_in_qa": round(h, 1),
                })

        # Capacity-vs-time model
        throughput = context.get("qa_throughput", settings.qa_throughput_default) or settings.qa_throughput_default
        qa_queue_count = len(qa_stories)
        backlog_clear_days = qa_queue_count / throughput if throughput > 0 else qa_queue_count
        days_left = days_remaining(sprint)
        # v3 5x5 matrix: probability = can QA clear the queue in the time left;
        # impact = queue size, nudged up when stories are already stuck.
        clear_ratio = (backlog_clear_days / days_left) if days_left > 0 else float(qa_queue_count)
        probability = qa_p(clear_ratio)
        impact = qa_i(qa_queue_count, len(stuck_stories))

        risks.append(self._emit(
            "QA_BOTTLENECK",
            None, 80,
            (
                f"{qa_queue_count} stories in QA Review. Consider adding QA "
                f"resource or running parallel testing."
            ),
            probability=probability,
            impact=impact,
            sprint_key=sprint.get("name") if sprint else None,
            issue_keys=[s.get("key") for s in qa_stories],
            qa_stories_count=qa_queue_count,
            qa_throughput_per_day=round(throughput, 2),
            backlog_clear_days=round(backlog_clear_days, 2),
            days_remaining=days_left,
            stuck_stories=stuck_stories,
        ))

        return risks

    # ------------------------------------------------------------------ #
    # 4. EXTERNAL_DEPENDENCY (ticket-level)
    # ------------------------------------------------------------------ #
    def detect_external_dependencies(self, issues, context=None):
        risks = []
        context = context or {}
        avg_sp = context.get("avg_sp", 0.0)
        blocking_map = context.get("blocking_map", {})

        for issue in issues:
            description = (issue.get("description") or "").lower()
            if not any(k in description for k in TRIGGER_KEYWORDS):
                continue
            if is_done(issue.get("status")):
                continue

            if any(k in description for k in EXTERNAL_KEYWORDS):
                dep_base = DEPENDENCY_BASE["external"]
            elif any(k in description for k in INTERNAL_KEYWORDS):
                dep_base = DEPENDENCY_BASE["internal"]
            else:
                dep_base = DEPENDENCY_BASE["default"]

            factors = {
                "fan_out": settings.fan_out_factor if issue.get("key") in blocking_map else 1.0,
                "size": size_weight(issue.get("story_points") or 0, avg_sp),
            }
            raw = self._apply_recipe(dep_base, RISK_RECIPES["EXTERNAL_DEPENDENCY"], factors)

            risks.append(self._emit(
                "EXTERNAL_DEPENDENCY",
                raw, 75,
                (
                    f"Story '{issue['key']}' has external dependency. "
                    f"Verify status and escalate if blocked."
                ),
                issue_key=issue["key"],
                summary=issue.get("summary"),
                dependency_detail=issue.get("blocked_by", "External dependency mentioned in description"),
                dependency_base=dep_base,
                fan_out=factors["fan_out"],
            ))

        return risks

    # ------------------------------------------------------------------ #
    # 5. DUE_DATE_PASSED (sprint-level, per-ticket max aggregate)
    # ------------------------------------------------------------------ #
    def detect_due_date_risks(self, sprint, issues, context=None, jira_timezone=None):
        risks = []
        context = context or {}
        avg_sp = context.get("avg_sp", 0.0)
        blocking_map = context.get("blocking_map", {})
        # Calendar date in the Jira timezone, so "overdue" matches what the user
        # sees in Jira (the server may run in a different zone, e.g. UTC vs +5:45).
        tz = _resolve_tz(jira_timezone)
        today = now_utc().astimezone(tz).date()
        overdue = []

        for issue in issues:
            due_date = issue.get("due_date")
            if not due_date or is_done(issue.get("status")):
                continue
            try:
                due = date.fromisoformat(str(due_date).split("T")[0])
            except ValueError:
                continue
            if due >= today:
                continue

            days_overdue = (today - due).days
            base = min(settings.due_date_base_cap, days_overdue * settings.due_date_base_per_day)
            factors = {
                "stage": workflow_stage_weight(issue.get("status")),
                "size": size_weight(issue.get("story_points") or 0, avg_sp),
                "blocking": settings.blocking_factor if issue.get("key") in blocking_map else 1.0,
            }
            raw = self._apply_recipe(base, RISK_RECIPES["DUE_DATE_PASSED"], factors)

            overdue.append({
                "key": issue["key"],
                "summary": issue.get("summary"),
                "due_date": due_date,
                "assignee": issue.get("assignee"),
                "days_overdue": days_overdue,
                "stage_weight": factors["stage"],
                "size_weight": round(factors["size"], 3),
                "is_blocking": issue.get("key") in blocking_map,
                "raw_score": round(raw, 1),
                "risk_score": cap_score(raw),
            })

        if not overdue:
            return risks

        raw = max(o["raw_score"] for o in overdue)

        risks.append(self._emit(
            "DUE_DATE_PASSED",
            raw, 85,
            (
                f"{len(overdue)} ticket(s) are past their due date. "
                f"Escalate immediately and reassign to unblock delivery."
            ),
            sprint_key=sprint.get("name") if sprint else None,
            issue_keys=[o["key"] for o in overdue],
            overdue_issues=overdue,
            count=len(overdue),
        ))

        return risks

    # ------------------------------------------------------------------ #
    # 6. BUG_RAISED (ticket-level, in-sprint defect)
    # ------------------------------------------------------------------ #
    def detect_bug_risks(self, sprint, issues, context=None):
        """Flag bugs raised during the current sprint (in-sprint defects).

        Sprint membership is guaranteed by the fetch JQL; the created-date
        comparison is the ISD discriminator (defect injected by this sprint,
        not backlog debt pulled in).

        Scoring follows the defect quality-risk band model: each bug's tier
        (P1-P4, derived from its Jira priority) maps to a score band, and the
        position within the band ramps with age across the sprint duration.
        Fixed P1s stay visible at a lower band; fixed P2+ drop out. A P1
        labeled as a production escape scores the maximum.
        """
        risks = []
        sprint_start = to_utc(sprint.get("startDate")) if sprint else None
        sprint_end = to_utc(sprint.get("endDate")) if sprint else None
        now = now_utc()
        sprint_days = None
        if sprint_start and sprint_end and sprint_end > sprint_start:
            sprint_days = max(1.0, (sprint_end - sprint_start).total_seconds() / 86400)

        for issue in issues:
            itype = (issue.get("issue_type") or "").strip().lower()
            if itype != "bug":
                continue
            status = issue.get("status")
            created = to_utc(issue.get("created"))
            if not created or not sprint_start or created < sprint_start:
                continue

            priority = issue.get("priority") or ""
            tier = settings.bug_priority_tiers.get(priority, settings.bug_default_tier)

            labels = [str(lb).strip().lower() for lb in (issue.get("labels") or [])]
            escaped = tier == "P1" and any(
                lb in settings.bug_prod_escape_labels for lb in labels
            )

            done = is_done(status)
            if done and tier != "P1":
                continue  # fixed lower-tier defects are normal quality variation
            if done and sprint_end and now > sprint_end:
                continue  # sprint ended and defect contained - no active risk

            days_old = max(0.0, (now - created).total_seconds() / 86400)
            if escaped:
                band_name = "P1_escaped"
                raw = float(settings.bug_p1_escaped_score)
            else:
                if tier == "P1":
                    band_name = "P1_fixed" if done else "P1_open"
                else:
                    band_name = tier
                low, high = settings.bug_tier_bands[band_name]
                frac = min(1.0, days_old / sprint_days) if sprint_days else 0.0
                raw = low + (high - low) * frac

            if escaped:
                recommendation = (
                    f"P1 defect '{issue.get('key')}' escaped to production. "
                    f"Investigate the escape immediately, add a regression test "
                    f"and review release/QA gates."
                )
            elif tier == "P1" and done:
                recommendation = (
                    f"P1 defect '{issue.get('key')}' was raised this sprint and "
                    f"fixed ({int(days_old)}d old). Contained before sprint end - "
                    f"verify the fix and watch for regressions."
                )
            elif tier == "P1":
                recommendation = (
                    f"P1 defect '{issue.get('key')}' raised this sprint is still "
                    f"open ({int(days_old)}d). Very high Definition-of-Done risk - "
                    f"prioritize the fix above new scope."
                )
            else:
                recommendation = (
                    f"{tier} defect '{issue.get('key')}' raised during the sprint "
                    f"({priority or 'unprioritized'}, {int(days_old)}d old). "
                    f"Triage and fix forward to protect the sprint goal."
                )

            risks.append(self._emit(
                "BUG_RAISED",
                raw, 80,
                recommendation,
                sprint_key=sprint.get("name") if sprint else None,
                issue_key=issue.get("key"),
                issue_keys=[issue.get("key")],
                summary=issue.get("summary"),
                assignee=issue.get("assignee"),
                status=status,
                priority=priority,
                tier=tier,
                band=band_name,
                days_since_created=round(days_old, 1),
            ))

        return risks

    # ------------------------------------------------------------------ #
    # 7. SCOPE_CREEP (sprint-level, vs first-active-sync baseline)
    # ------------------------------------------------------------------ #
    def detect_scope_creep(self, sprint, issues, context=None):
        """Flag scope added or re-estimated upward after sprint start.

        The baseline (total SP + per-issue SP map) is captured automatically
        on the first sync while the sprint is active — that is treated as the
        planning commitment. Growth beyond settings.scope_creep_min_growth_pct,
        any issue added post-baseline, or any estimate hike triggers the risk.
        Requires at least two scope history points so single-sync noise and
        future sprints (no baseline) are skipped.
        """
        risks = []
        context = context or {}
        name = sprint.get("name") if sprint else None
        if not name:
            return risks

        baseline = context.get("scope_baseline")
        history = context.get("scope_history") or []
        if not baseline or len(history) < 2:
            return risks

        current_sp = sum(issue.get("story_points", 0) or 0 for issue in issues)
        baseline_sp = float(baseline.get("total_sp") or 0)
        baseline_issues = baseline.get("issues") or {}

        current_by_key = {
            issue.get("key"): (issue.get("story_points", 0) or 0)
            for issue in issues
            if issue.get("key")
        }
        added = [
            {
                "key": issue["key"],
                "summary": issue.get("summary"),
                "sp": current_by_key[issue["key"]],
            }
            for issue in issues
            if issue.get("key") and issue["key"] not in baseline_issues
        ]
        hiked = [
            {"key": key, "from": base_sp, "to": current_by_key[key]}
            for key, base_sp in baseline_issues.items()
            if key in current_by_key and current_by_key[key] > (base_sp or 0)
        ]

        growth = ((current_sp - baseline_sp) / baseline_sp * 100) if baseline_sp > 0 else 0.0
        min_growth = settings.scope_creep_min_growth_pct
        if growth < min_growth and not added and not hiked:
            return risks

        # v3 5x5 matrix: creep has already happened (P=5); impact from growth
        # magnitude, floored at Moderate when work was added or re-estimated.
        probability = scope_creep_p()
        impact = scope_creep_i(growth, added or hiked)
        confidence = 60 if baseline.get("late_capture") else 75

        parts = [f"Sprint scope grew {growth:.0f}% since planning ({baseline_sp:.0f} → {current_sp} SP)."]
        if added:
            keys = ", ".join(a["key"] for a in added[:3])
            more = "" if len(added) <= 3 else f" (+{len(added) - 3} more)"
            parts.append(f"{len(added)} issue(s) added after start ({keys}{more}).")
        if hiked:
            hikes_txt = ", ".join(f"{h['key']} {h['from']}→{h['to']}" for h in hiked[:3])
            more = "" if len(hiked) <= 3 else f" (+{len(hiked) - 3} more)"
            parts.append(f"Estimates raised: {hikes_txt}{more}.")
        parts.append(
            "Renegotiate scope with stakeholders or add capacity — do not "
            "silently absorb the extra work."
        )

        risks.append(self._emit(
            "SCOPE_CREEP",
            None, confidence,
            " ".join(parts),
            # Unplanned work absorbed mid-sprint is always a red flag, even +1 SP:
            # floor the displayed score so severity lands in CRITICAL.
            score_floor=settings.scope_creep_floor_score,
            probability=probability,
            impact=impact,
            sprint_key=name,
            issue_keys=[a["key"] for a in added] + [h["key"] for h in hiked],
            baseline_sp=baseline_sp,
            current_sp=current_sp,
            growth_percent=round(growth, 1),
            added_issues=added,
            story_point_hikes=hiked,
            late_baseline=bool(baseline.get("late_capture")),
        ))

        return risks

    # ------------------------------------------------------------------ #
    # Sprint-level: ended but incomplete (deadline passed, work remains)
    # ------------------------------------------------------------------ #
    def detect_sprint_overdue_risk(self, sprint, issues, context=None, jira_timezone=None):
        """Flag a sprint whose end date has passed but Jira still lists it as
        active (not closed).

        Without this, an ended sprint can show "no risk" because every
        issue-level detector already assumes an in-flight sprint. It fires when
        the sprint is past its end date: either work remains incomplete, or all
        work is Done but the sprint was never closed in Jira.
        """
        risks = []
        if not sprint:
            return risks
        end = to_utc(sprint.get("endDate"))
        if not end:
            return risks
        now = now_utc()
        if now <= end:
            return risks

        total_sp = sum(i.get("story_points", 0) or 0 for i in issues)
        completed_sp = sum(
            i.get("story_points", 0) or 0
            for i in issues
            if is_done(i.get("status"))
        )
        remaining_sp = total_sp - completed_sp
        # Calendar-day difference in the JIRA timezone (from /myself), so it
        # matches what the user sees in Jira regardless of where this server runs.
        # Jira stores endDate in UTC, so a sprint "ending Aug 24" can serialize to
        # Aug 23 18:15Z and read as Aug 23 in UTC. Using the Jira timezone keeps
        # sprints that end on the same board date aligned with the frontend label
        # and the Jira UI.
        tz = _resolve_tz(jira_timezone)
        days_overdue = max(1, (now.astimezone(tz).date() - end.astimezone(tz).date()).days)

        if remaining_sp > 0:
            recommendation = (
                f"Sprint ended {days_overdue} day(s) ago but {remaining_sp:.0f} SP remain "
                f"incomplete and it is still open in Jira. Close it out or move the "
                f"unfinished work to the next sprint."
            )
        else:
            # All work is Done in Jira, but the sprint was never closed.
            recommendation = (
                f"Sprint ended {days_overdue} day(s) ago but is still open ('active') in "
                f"Jira even though all {total_sp:.0f} SP are Done. Close the sprint to "
                f"keep reporting clean."
            )
        # v3 5x5 matrix: the sprint has already ended, so the risk has
        # materialised (P=5); impact is the share of the sprint left unfinished.
        probability = sprint_ended_p()
        impact = sprint_ended_i(remaining_sp, total_sp)
        risks.append(self._emit(
            "SPRINT_ENDED_INCOMPLETE",
            None, 90,
            recommendation,
            probability=probability,
            impact=impact,
            sprint_key=sprint.get("name"),
            issue_keys=[i.get("key") for i in issues if not is_done(i.get("status"))],
            total_sp=total_sp,
            completed_sp=completed_sp,
            remaining_sp=remaining_sp,
            days_overdue=days_overdue,
        ))
        return risks

    # ------------------------------------------------------------------ #
    # Next-sprint (pre-planning) — v1 formulas, out of v2 rubric scope
    # ------------------------------------------------------------------ #
    def calculate_next_sprint_risks(self, issues):
        risks = []

        unassigned = [i for i in issues if not i.get("assignee") or i.get("assignee") == "Unassigned"]
        if unassigned:
            risks.append({
                "type": "UNASSIGNED",
                "issue_keys": [i["key"] for i in unassigned],
                "count": len(unassigned),
                "risk_score": min(100, 40 + len(unassigned) * 10),
                "confidence": 85,
                "severity": "HIGH" if len(unassigned) >= 3 else "MEDIUM",
                "recommendation": (
                    f"{len(unassigned)} issue(s) have no assignee. Assign owners before planning to avoid capacity gaps."
                ),
            })

        unestimated = [i for i in issues if not i.get("story_points") or i.get("story_points") == 0]
        if unestimated:
            risks.append({
                "type": "UNESTIMATED",
                "issue_keys": [i["key"] for i in unestimated],
                "count": len(unestimated),
                "risk_score": min(100, 35 + len(unestimated) * 8),
                "confidence": 80,
                "severity": "MEDIUM",
                "recommendation": (
                    f"{len(unestimated)} issue(s) lack story points. Estimate during planning to size the sprint accurately."
                ),
            })

        no_desc = [i for i in issues if not (i.get("description") or "").strip()]
        thin_ac = [
            i for i in issues
            if (i.get("description") or "").strip() and len((i.get("acceptance_criteria") or "").strip()) < 30
        ]
        undefined = no_desc + thin_ac
        if undefined:
            risks.append({
                "type": "UNDEFINED_SCOPE",
                "issue_keys": [i["key"] for i in undefined],
                "count": len(undefined),
                "risk_score": min(100, 30 + len(undefined) * 8),
                "confidence": 75,
                "severity": "MEDIUM",
                "recommendation": (
                    f"{len(undefined)} issue(s) lack defined acceptance criteria. "
                    "Align with Business/PO and clarify Acceptance Criteria before planning."
                ),
            })

        oversized = [i for i in issues if (i.get("story_points") or 0) >= 8]
        if oversized:
            risks.append({
                "type": "SIZING_RISK",
                "issue_keys": [i["key"] for i in oversized],
                "count": len(oversized),
                "risk_score": min(100, 50 + len(oversized) * 15),
                "confidence": 70,
                "severity": "MEDIUM",
                "recommendation": (
                    f"{len(oversized)} issue(s) are large (>=8 SP). Break them down before committing to the sprint."
                ),
            })

        dep_risks = self.detect_external_dependencies(issues)
        risks.extend(dep_risks)

        due_risks = self.detect_due_date_risks(None, issues)
        risks.extend(due_risks)

        return sorted(risks, key=lambda x: x.get("risk_score", 0), reverse=True)

    # ------------------------------------------------------------------ #
    # Aggregation helpers
    # ------------------------------------------------------------------ #
    def aggregate_risk_score(self, risks):
        scores = sorted([r.get("risk_score", 0) for r in risks], reverse=True)
        if not scores:
            return 0
        score = scores[0]
        for s in scores[1:]:
            score += s * 0.2
        return min(100, int(round(score)))

    def generate_risk_summary(self, risks):
        high_risks = [r for r in risks if r["severity"] in ("CRITICAL", "HIGH")]
        medium_risks = [r for r in risks if r["severity"] == "MEDIUM"]

        return {
            "total_risks": len(risks),
            "high_severity": len(high_risks),
            "medium_severity": len(medium_risks),
            "overall_sprint_health": max(0, 100 - (len(high_risks) * 20 + len(medium_risks) * 10)),
        }


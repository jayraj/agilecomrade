import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risk_engine import RiskEngine
from snapshot import _build_delivery_health


def test_delivery_health_forecast_handles_timezone_aware_dates():
    sprint = {
        "name": "S1",
        "startDate": "2024-01-01T00:00:00Z",
        "endDate": "2024-01-15T00:00:00Z",
    }
    sprint_data = {
        "P": {
            "sprint": sprint,
            "issues": [{"key": "A", "story_points": 5, "status": "In Progress"}],
        }
    }
    velocity_data = {"P": [{"completed_sp": 10}, {"completed_sp": 10}]}

    projects = _build_delivery_health(sprint_data, {}, velocity_data, [], RiskEngine())

    assert projects
    project = projects[0]
    assert project["forecast_date"] is not None
    # Now is well past the sprint end, so the forecast must be flagged late
    # rather than raising on a naive/aware datetime comparison.
    assert isinstance(project["forecast_delay_days"], int)
    assert project["forecast_delay_days"] > 0
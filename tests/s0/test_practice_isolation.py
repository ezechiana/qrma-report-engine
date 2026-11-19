"""Exercise existing ownership boundaries before clinic membership migration."""

import os

import pytest


def assert_report_scope(response, expected_ids):
    assert response.status_code == 200
    assert {report["id"] for report in response.json()} == expected_ids, "Cross-practice report disclosure"


@pytest.mark.parametrize("clinic,owner", [("clinic_a", "owner_a"), ("clinic_b", "owner_b")])
def test_owner_lists_only_own_reports(two_practices, clinic, owner):
    fixture = two_practices
    fixture.actor["user"] = fixture.actors[owner]
    expected_clinic = clinic
    if os.environ.get("GO360_S0_INJECT_WRONG_CLINIC") == "1":
        expected_clinic = "clinic_b" if clinic == "clinic_a" else "clinic_a"
    assert_report_scope(fixture.http.get("/api/reports"), {str(fixture.reports[expected_clinic].id)})


@pytest.mark.parametrize("actor,clinic", [
    ("owner_a", "clinic_b"), ("owner_b", "clinic_a"),
    ("outsider", "clinic_a"), ("outsider", "clinic_b"),
])
def test_wrong_practice_detail_export_and_mutation_are_denied(two_practices, actor, clinic):
    fixture = two_practices
    fixture.actor["user"] = fixture.actors[actor]
    report = fixture.reports[clinic]
    for suffix in ("", "/json", "/pdf", "/html", "/status"):
        response = fixture.http.get(f"/api/reports/{report.id}{suffix}", follow_redirects=False)
        assert response.status_code == 404
        assert response.json() == {"detail": "Report not found"}
    response = fixture.http.post(f"/api/reports/{report.id}/archive")
    assert response.status_code == 404
    fixture.db.refresh(report)
    assert not report.is_archived


def test_pdf_and_release_withdrawal_preserve_report_and_share(two_practices, record_property):
    fixture = two_practices
    report = fixture.reports["clinic_a"]
    chart = fixture.charts["clinic_a"]
    response = fixture.http.get(f"/api/reports/{report.id}/pdf", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "https://example.invalid/clinic_a/report.pdf"
    path = f"/api/patients/{chart.id}/report-releases/{report.id}"
    for released in (True, False):
        response = fixture.http.post(path, json={"released": released})
        assert response.status_code == 200
        assert response.json()["released"] is released
    fixture.db.refresh(report)
    fixture.db.refresh(fixture.shares["clinic_a"])
    assert report.status.value == "ready"
    assert fixture.shares["clinic_a"].is_active
    record_property("fixture_report_id", str(report.id))
    record_property("fixture_case_id", str(report.case_id))
    record_property("fixture_patient_id", str(chart.id))


def test_wrong_clinic_release_is_denied(two_practices):
    fixture = two_practices
    chart = fixture.charts["clinic_b"]
    report = fixture.reports["clinic_b"]
    response = fixture.http.post(
        f"/api/patients/{chart.id}/report-releases/{report.id}", json={"released": True},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Patient not found"}


def test_incorrect_expected_clinic_fixture_fails_the_guard(two_practices):
    response = two_practices.http.get("/api/reports")
    with pytest.raises(AssertionError, match="Cross-practice report disclosure"):
        assert_report_scope(response, {str(two_practices.reports["clinic_b"].id)})


def test_actor_without_membership_has_an_empty_report_list(two_practices):
    fixture = two_practices
    fixture.actor["user"] = fixture.actors["outsider"]
    assert_report_scope(fixture.http.get("/api/reports"), set())

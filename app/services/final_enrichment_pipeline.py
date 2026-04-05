# app/services/final_enrichment_pipeline.py

from app.services.recommendation_engine import apply_recommendation_engine
from app.services.ai_practitioner_intelligence import apply_practitioner_intelligence


def apply_final_report_enrichment(report):
    """
    Final enrichment stage before rendering the report.

    Order matters:
    1. Apply recommendation engine first so clinical/product recommendations
       are attached to the report.
    2. Apply practitioner intelligence after that so the AI layer can see
       the enriched structure and existing recommendations when generating:
       - practitioner_summary
       - key_patterns
       - priority_actions
    """
    report = apply_recommendation_engine(report)
    report = apply_practitioner_intelligence(report)
    return report
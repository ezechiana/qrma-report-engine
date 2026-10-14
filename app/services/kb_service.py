from __future__ import annotations

from typing import Any
from sqlalchemy import text
from sqlalchemy.orm import Session


MARKER_SQL = text("""
SELECT
    m.id AS marker_id,
    m.source_control_id,
    m.canonical_control_id,
    m.go360_id,
    m.qrma_id,
    m.source_qrma_term_en AS qrma_term,
    m.canonical_title AS go360_title,
    s.canonical_name AS system_name,
    s.slug AS system_slug,
    mc.clinical_meaning,
    mc.why_it_matters,
    mc.functional_significance,
    mc.low_interpretation,
    mc.balanced_interpretation,
    mc.high_interpretation,
    mc.pattern_links_text,
    mc.recommendation_hint,
    mc.explain_to_client,
    mc.clinical_pearl,
    mc.critical_distinction,
    mc.editorial_status
FROM knowledge.markers m
JOIN knowledge.systems s ON s.id = m.system_id
LEFT JOIN knowledge.marker_content mc ON mc.marker_id = m.id
LEFT JOIN knowledge.languages l ON l.id = mc.language_id
WHERE UPPER(m.go360_id) = UPPER(:go360_id)
  AND (l.code = :language_code OR mc.id IS NULL)
ORDER BY mc.revision_no DESC NULLS LAST
LIMIT 1
""")

REPORT_SQL = text("""
SELECT
    rt.code AS report_type_code,
    rt.name AS report_type,
    rs.section_name,
    rs.display_order AS section_order,
    rmm.display_order AS marker_order
FROM knowledge.qrma_report_marker_map rmm
JOIN knowledge.qrma_report_sections rs ON rs.id = rmm.report_section_id
JOIN knowledge.qrma_report_types rt ON rt.id = rs.report_type_id
WHERE rmm.marker_id = :marker_id
  AND rmm.is_present = TRUE
ORDER BY rt.display_order, rs.display_order, rmm.display_order
""")

BANDS_SQL = text("""
SELECT
    classification_model,
    direction,
    severity,
    software_symbol,
    range_type,
    lower_bound,
    upper_bound,
    operator,
    display_text,
    rule_note,
    verification_status,
    evidence_ref
FROM knowledge.marker_reference_bands
WHERE marker_id = :marker_id
ORDER BY
    CASE severity
        WHEN 'normal' THEN 1
        WHEN 'mild' THEN 2
        WHEN 'moderate' THEN 3
        WHEN 'severe' THEN 4
        WHEN 'directional' THEN 5
        ELSE 6
    END,
    CASE direction
        WHEN 'low' THEN 1
        WHEN 'normal' THEN 2
        WHEN 'high' THEN 3
        ELSE 4
    END,
    lower_bound NULLS FIRST,
    upper_bound NULLS LAST
""")

PATTERNS_SQL = text("""
SELECT pc.slug, pc.name, pc.description, mpc.position
FROM knowledge.marker_pattern_clusters mpc
JOIN knowledge.pattern_clusters pc ON pc.id = mpc.pattern_cluster_id
WHERE mpc.marker_id = :marker_id
ORDER BY mpc.position NULLS LAST, pc.name
""")

INFLUENCES_SQL = text("""
SELECT
    f.slug,
    f.name,
    COALESCE(mi.note, f.description) AS note,
    mi.direction,
    mi.position
FROM knowledge.marker_influences mi
JOIN knowledge.influencing_factors f ON f.id = mi.influencing_factor_id
WHERE mi.marker_id = :marker_id
ORDER BY mi.position NULLS LAST, f.name
""")

DIAGNOSTICS_SQL = text("""
SELECT
    d.slug,
    d.name,
    d.description,
    md.role,
    md.note,
    md.position
FROM knowledge.marker_diagnostics md
JOIN knowledge.diagnostic_items d ON d.id = md.diagnostic_item_id
WHERE md.marker_id = :marker_id
ORDER BY md.position NULLS LAST, d.name
""")

MEDIA_SQL = text("""
SELECT
    ma.id, ma.asset_type, ma.asset_role, ma.audience, ma.title,
    ma.caption, ma.alt_text, ma.url, ma.storage_path, ma.platform,
    ma.mime_type, ma.source_reference, ma.source_marker_artifact,
    ma.contains_embedded_text, ma.reusable_across_languages,
    COALESCE(pas.position, ma.display_order, aml.position) AS display_order
FROM knowledge.media_assets ma
JOIN knowledge.asset_marker_links aml
  ON aml.asset_kind = 'media'
 AND aml.asset_id = ma.id
 AND aml.marker_id = :marker_id
LEFT JOIN knowledge.publications p ON p.code = 'KB_WEB_EN'
LEFT JOIN knowledge.publication_asset_scope pas
  ON pas.publication_id = p.id
 AND pas.media_asset_id = ma.id
WHERE ma.status IN ('review','published')
  AND (pas.include_in_output IS NULL OR pas.include_in_output = TRUE)
ORDER BY COALESCE(pas.position, ma.display_order, aml.position) NULLS LAST, ma.title
""")

SYSTEMS_SQL = text("""
SELECT
    s.slug,
    s.canonical_name AS name,
    count(m.id) AS marker_count
FROM knowledge.systems s
JOIN knowledge.markers m ON m.system_id = s.id
WHERE s.active = TRUE
  AND m.is_publishable_marker = TRUE
  AND m.status = 'active'
GROUP BY s.id, s.slug, s.canonical_name, s.publication_order
ORDER BY s.publication_order NULLS LAST, s.canonical_name
""")

PATTERN_COLLECTIONS_SQL = text("""
SELECT
    pc.slug,
    pc.name,
    pc.description,
    count(mpc.marker_id) AS marker_count
FROM knowledge.pattern_clusters pc
JOIN knowledge.marker_pattern_clusters mpc ON mpc.pattern_cluster_id = pc.id
GROUP BY pc.id, pc.slug, pc.name, pc.description
ORDER BY pc.name
""")

REPORT_TYPES_SQL = text("""
SELECT
    rt.code,
    rt.name,
    rt.display_order,
    count(rmm.marker_id) AS marker_occurrences
FROM knowledge.qrma_report_types rt
JOIN knowledge.qrma_report_sections rs ON rs.report_type_id = rt.id
JOIN knowledge.qrma_report_marker_map rmm ON rmm.report_section_id = rs.id
WHERE rmm.is_present = TRUE
GROUP BY rt.id, rt.code, rt.name, rt.display_order
ORDER BY rt.display_order
""")

SOFTWARE_SECTIONS_SQL = text("""
SELECT
    rt.code AS report_type_code,
    rt.name AS report_type,
    rs.id AS section_id,
    rs.section_name,
    rs.display_order,
    count(rmm.marker_id) AS marker_count
FROM knowledge.qrma_report_types rt
JOIN knowledge.qrma_report_sections rs ON rs.report_type_id = rt.id
JOIN knowledge.qrma_report_marker_map rmm ON rmm.report_section_id = rs.id
WHERE rt.code = :report_type_code
  AND rmm.is_present = TRUE
GROUP BY rt.code, rt.name, rs.id, rs.section_name, rs.display_order
ORDER BY rs.display_order
""")

SOFTWARE_SECTION_MARKERS_SQL = text("""
SELECT
    rt.code AS report_type_code,
    rt.name AS report_type,
    rs.id AS section_id,
    rs.section_name,
    rmm.display_order AS marker_order,
    m.go360_id,
    m.qrma_id,
    m.canonical_title AS go360_title,
    m.source_qrma_term_en AS qrma_term
FROM knowledge.qrma_report_types rt
JOIN knowledge.qrma_report_sections rs ON rs.report_type_id = rt.id
JOIN knowledge.qrma_report_marker_map rmm ON rmm.report_section_id = rs.id
JOIN knowledge.markers m ON m.id = rmm.marker_id
WHERE rt.code = :report_type_code
  AND rs.id = :section_id
  AND rmm.is_present = TRUE
ORDER BY rmm.display_order
""")

AZ_SQL = text("""
SELECT go360_id, go360_title, qrma_term, system_name, system_slug
FROM knowledge.v_web_marker_az_en
ORDER BY lower(go360_title), go360_id
""")

SYSTEM_INDEX_SQL = text("""
SELECT go360_id, go360_title, qrma_term, system_name, system_slug
FROM knowledge.v_web_system_index_en
WHERE system_slug = :slug
ORDER BY go360_title
""")

PATTERN_INDEX_SQL = text("""
SELECT go360_id, go360_title, pattern_cluster_name, pattern_cluster_slug
FROM knowledge.v_web_pattern_index_en
WHERE pattern_cluster_slug = :slug
ORDER BY go360_title
""")

SEARCH_SQL = text("""
WITH q AS (
    SELECT lower(trim(:q)) AS term
),
english AS (
    SELECT id AS language_id
    FROM knowledge.languages
    WHERE code = 'en'
),
marker_base AS (
    SELECT
        m.id AS marker_id,
        m.go360_id,
        m.qrma_id,
        m.canonical_title,
        m.source_qrma_term_en,
        s.canonical_name AS system_name,
        mc.clinical_meaning,
        mc.pattern_links_text
    FROM knowledge.markers m
    JOIN knowledge.systems s ON s.id = m.system_id
    LEFT JOIN english e ON TRUE
    LEFT JOIN LATERAL (
        SELECT mc2.*
        FROM knowledge.marker_content mc2
        WHERE mc2.marker_id = m.id
          AND mc2.language_id = e.language_id
        ORDER BY mc2.revision_no DESC NULLS LAST
        LIMIT 1
    ) mc ON TRUE
    WHERE m.is_publishable_marker = TRUE
      AND m.status = 'active'
),
alias_agg AS (
    SELECT
        msa.marker_id,
        string_agg(lower(msa.alias), ' | ') AS aliases
    FROM knowledge.marker_search_aliases msa
    JOIN english e ON e.language_id = msa.language_id
    WHERE msa.status IN ('reviewed','approved','published')
    GROUP BY msa.marker_id
),
marker_matches AS (
    SELECT
        'marker'::text AS result_type,
        mb.go360_id AS key,
        mb.canonical_title AS title,
        mb.go360_id || COALESCE(' · ' || mb.qrma_id, '') AS id_line,
        'QRMA: ' || mb.source_qrma_term_en AS subtitle,
        mb.system_name AS meta,
        '/kb/' || mb.go360_id AS url,
        CASE
            WHEN lower(mb.go360_id) = q.term THEN 1000
            WHEN lower(mb.canonical_title) = q.term THEN 990
            WHEN lower(COALESCE(mb.qrma_id,'')) = q.term THEN 955
            WHEN lower(mb.source_qrma_term_en) = q.term THEN 950
            WHEN lower(mb.go360_id) LIKE q.term || '%' THEN 930
            WHEN lower(mb.canonical_title) LIKE q.term || '%' THEN 920
            WHEN lower(COALESCE(mb.qrma_id,'')) LIKE q.term || '%' THEN 905
            WHEN lower(mb.source_qrma_term_en) LIKE q.term || '%' THEN 900
            WHEN lower(COALESCE(aa.aliases,'')) LIKE '%' || q.term || '%' THEN 860
            WHEN lower(mb.canonical_title) LIKE '%' || q.term || '%' THEN 840
            WHEN lower(mb.source_qrma_term_en) LIKE '%' || q.term || '%' THEN 830
            WHEN lower(COALESCE(mb.clinical_meaning,'')) LIKE '%' || q.term || '%' THEN 620
            WHEN lower(COALESCE(mb.pattern_links_text,'')) LIKE '%' || q.term || '%' THEN 600
            ELSE 0
        END AS rank,
        CASE
            WHEN lower(mb.go360_id) = q.term
              OR lower(mb.canonical_title) = q.term
            THEN 'strong'
            WHEN lower(COALESCE(mb.qrma_id,'')) = q.term
              OR lower(mb.source_qrma_term_en) = q.term
            THEN 'qrma'
            WHEN lower(mb.go360_id) LIKE q.term || '%'
              OR lower(mb.canonical_title) LIKE q.term || '%'
              OR lower(COALESCE(mb.qrma_id,'')) LIKE q.term || '%'
              OR lower(mb.source_qrma_term_en) LIKE q.term || '%'
              OR lower(COALESCE(aa.aliases,'')) LIKE '%' || q.term || '%'
              OR lower(mb.canonical_title) LIKE '%' || q.term || '%'
              OR lower(mb.source_qrma_term_en) LIKE '%' || q.term || '%'
            THEN 'direct'
            ELSE 'contextual'
        END AS match_group
    FROM marker_base mb
    CROSS JOIN q
    LEFT JOIN alias_agg aa ON aa.marker_id = mb.marker_id
    WHERE
        lower(mb.go360_id) LIKE '%' || q.term || '%'
        OR lower(COALESCE(mb.qrma_id,'')) LIKE '%' || q.term || '%'
        OR lower(mb.canonical_title) LIKE '%' || q.term || '%'
        OR lower(mb.source_qrma_term_en) LIKE '%' || q.term || '%'
        OR lower(COALESCE(aa.aliases,'')) LIKE '%' || q.term || '%'
        OR lower(COALESCE(mb.clinical_meaning,'')) LIKE '%' || q.term || '%'
        OR lower(COALESCE(mb.pattern_links_text,'')) LIKE '%' || q.term || '%'
),
system_matches AS (
    SELECT
        'system'::text AS result_type,
        s.slug AS key,
        s.canonical_name AS title,
        NULL::text AS id_line,
        count(m.id)::text || ' markers' AS subtitle,
        'Go360 clinical system'::text AS meta,
        '/kb/system/' || s.slug AS url,
        CASE
            WHEN lower(s.canonical_name) = q.term THEN 945
            WHEN lower(s.canonical_name) LIKE q.term || '%' THEN 925
            ELSE 900
        END AS rank,
        'explore'::text AS match_group
    FROM knowledge.systems s
    JOIN knowledge.markers m ON m.system_id = s.id
    CROSS JOIN q
    WHERE s.active = TRUE
      AND m.is_publishable_marker = TRUE
      AND lower(s.canonical_name) LIKE '%' || q.term || '%'
    GROUP BY s.slug, s.canonical_name, q.term
),
pattern_matches AS (
    SELECT
        'pattern'::text AS result_type,
        pc.slug AS key,
        pc.name AS title,
        NULL::text AS id_line,
        count(mpc.marker_id)::text || ' related markers' AS subtitle,
        'Functional pattern'::text AS meta,
        '/kb/pattern/' || pc.slug AS url,
        CASE
            WHEN lower(pc.name) = q.term THEN 940
            WHEN lower(pc.name) LIKE q.term || '%' THEN 920
            ELSE 895
        END AS rank,
        'explore'::text AS match_group
    FROM knowledge.pattern_clusters pc
    JOIN knowledge.marker_pattern_clusters mpc ON mpc.pattern_cluster_id = pc.id
    CROSS JOIN q
    WHERE lower(pc.name) LIKE '%' || q.term || '%'
       OR lower(COALESCE(pc.description,'')) LIKE '%' || q.term || '%'
    GROUP BY pc.slug, pc.name, q.term
)
SELECT result_type, key, title, id_line, subtitle, meta, url, rank, match_group
FROM (
    SELECT * FROM marker_matches
    UNION ALL
    SELECT * FROM system_matches
    UNION ALL
    SELECT * FROM pattern_matches
) x
ORDER BY
    CASE match_group
        WHEN 'strong' THEN 1
        WHEN 'qrma' THEN 2
        WHEN 'explore' THEN 3
        WHEN 'direct' THEN 4
        ELSE 5
    END,
    rank DESC,
    title
LIMIT 100
""")


def rows(result) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in result.all()]


def get_marker_page(db: Session, go360_id: str, *, language_code: str = "en") -> dict[str, Any] | None:
    row = db.execute(MARKER_SQL, {"go360_id": go360_id.strip(), "language_code": language_code}).first()
    if not row:
        return None
    marker = dict(row._mapping)
    marker_id = marker["marker_id"]
    marker["report_memberships"] = rows(db.execute(REPORT_SQL, {"marker_id": marker_id}))
    marker["reference_bands"] = rows(db.execute(BANDS_SQL, {"marker_id": marker_id}))
    marker["pattern_clusters"] = rows(db.execute(PATTERNS_SQL, {"marker_id": marker_id}))
    marker["influences"] = rows(db.execute(INFLUENCES_SQL, {"marker_id": marker_id}))
    marker["diagnostics"] = rows(db.execute(DIAGNOSTICS_SQL, {"marker_id": marker_id}))
    marker["media_assets"] = rows(db.execute(MEDIA_SQL, {"marker_id": marker_id}))
    marker["populations"] = list(dict.fromkeys(x["report_type"] for x in marker["report_memberships"]))
    marker["language_code"] = language_code
    return marker


def get_portal_data(db: Session) -> dict[str, Any]:
    return {
        "systems": rows(db.execute(SYSTEMS_SQL)),
        "patterns": rows(db.execute(PATTERN_COLLECTIONS_SQL)),
        "report_types": rows(db.execute(REPORT_TYPES_SQL)),
    }


def search_knowledge(db: Session, query: str) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return []
    return rows(db.execute(SEARCH_SQL, {"q": query}))

def get_az_index(db: Session): return rows(db.execute(AZ_SQL))
def get_system_index(db: Session, slug: str): return rows(db.execute(SYSTEM_INDEX_SQL, {"slug": slug}))
def get_pattern_index(db: Session, slug: str): return rows(db.execute(PATTERN_INDEX_SQL, {"slug": slug}))
def get_software_sections(db: Session, report_type_code: str): return rows(db.execute(SOFTWARE_SECTIONS_SQL, {"report_type_code": report_type_code}))
def get_software_section_markers(db: Session, report_type_code: str, section_id: str): return rows(db.execute(SOFTWARE_SECTION_MARKERS_SQL, {"report_type_code": report_type_code, "section_id": section_id}))

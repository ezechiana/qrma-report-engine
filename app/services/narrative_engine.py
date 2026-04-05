from app.models.schema import ParsedReport


class NarrativeEngine:

    def generate(self, report: ParsedReport):

        patterns = report.patterns or []
        pattern_names = [p.name for p in patterns]

        # --- Opening ---
        opening = (
            f"Your scan highlights several functional patterns rather than isolated findings. "
            f"The strongest themes centre around {', '.join(pattern_names[:3])}, "
            f"suggesting the body is currently working under a degree of internal load."
        )

        # --- Core Story ---
        core = (
            "At a foundational level, the body appears to be balancing between demand and capacity. "
            "There are signs of internal load (such as metabolic, inflammatory, or detox burden), "
            "while capacity factors such as nutrient status and energy production may not be fully keeping pace."
        )

        # --- Root Cause ---
        root = (
            "When these patterns are viewed together, a likely sequence emerges. "
            "Digestive efficiency may influence nutrient availability, which impacts energy production, "
            "and in turn affects metabolic and systemic balance."
        )

        # --- Priority ---
        priority = (
            "The most impactful areas to focus on are:\n"
            "1. Digestive support\n"
            "2. Nutrient replenishment\n"
            "3. Metabolic balance\n"
            "Addressing these areas together typically produces the most noticeable improvements."
        )

        # --- Closing ---
        closing = (
            "Overall, the body is not showing signs of breakdown, but rather adaptation under load. "
            "With the right support, these patterns are often highly responsive."
        )

        return {
            "opening_summary": opening,
            "core_story": core,
            "root_cause_flow": root,
            "priority_focus": priority,
            "closing_summary": closing
        }
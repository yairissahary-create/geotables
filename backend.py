from PySide6.QtCore import QObject, Signal, Slot
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

DEFAULT_SPECIAL_CHARS = ["≠", "≤", "≥", "∦", "∠", "∥", "⟂", "⟂", "△", "≌", "∼"]
DEFAULT_FIND_CHARS = [
    "!=", "<=", ">=", "!prl'", "ngl'", "prl'", "||", "prp'",
    "|_", "tr'", "cng'", "~=", "=~", "sm'", "~"
]
DEFAULT_REPLACE_CHARS = [
    "≠", "≤", "≥", "∦", "∠", "∥", "∥", "⟂", "⟂", "△", "≌", "≌", "≌", "∼", "∼"
]

# Module-level fallback for any code that imports `special_chars` directly
# before a Backend instance exists. Kept in sync by Backend.__init__.
special_chars = list(DEFAULT_SPECIAL_CHARS)

# Category keyword -> matching polygon symbol, used by the טענה Enter-key
# redirect logic in app.py. Matched as a case-insensitive substring against
# each justification's categories, so it tolerates spelling variants
# ("quadrelaterals", "quadrilaterals", "quads", etc).
SHAPE_SYMBOLS = [
    ("triangle", "△"),
    ("quad", "◻"),
]


class Backend(QObject):
    textReplaced = Signal(str)
    suggestionReady = Signal(list)

    def __init__(self, data_dir=None):
        super().__init__()
        self.data_dir = Path(data_dir) if data_dir else SCRIPT_DIR
        self.config_path = self.data_dir / "config.json"

        self.justifications = self._load_justifications()
        names = []
        for item in self.justifications:
            for n in item.get("name", []) if isinstance(item.get("name", []), list) else []:
                names.append(n)
        self.justification_names = names

        self.find_chars, self.replace_chars, self.special_chars = self._load_chars_config()
        self.script_end_chars = self._build_script_end_chars()

        global special_chars
        special_chars = self.special_chars

    # ---- loading ----

    def _load_justifications(self):
        path = self.data_dir / "justifications.json"
        try:
            if path.exists():
                with path.open("r", encoding="utf-8") as file:
                    loaded = json.load(file)
                return loaded if isinstance(loaded, list) else []
        except Exception as exc:
            print(f"Warning: could not load justifications.json: {exc}")
        return []

    def _read_config(self):
        try:
            if self.config_path.exists():
                with self.config_path.open("r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _load_chars_config(self):
        data = self._read_config()
        find_chars = data.get("find_chars", list(DEFAULT_FIND_CHARS))
        replace_chars = data.get("replace_chars", list(DEFAULT_REPLACE_CHARS))
        special = data.get("special_chars", list(DEFAULT_SPECIAL_CHARS))
        return find_chars, replace_chars, special

    def _build_script_end_chars(self):
        return set([" ", "+", "-", "=", ")", "]", "}", ","] + self.special_chars)

    # ---- saving ----

    def save_chars_config(self):
        """Persist find/replace/special char lists into config.json,
        preserving any other keys (seq format, colors, data_dir) already there."""
        try:
            data = self._read_config()
            data["find_chars"] = self.find_chars
            data["replace_chars"] = self.replace_chars
            data["special_chars"] = self.special_chars
            with self.config_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
        except Exception as exc:
            print(f"Warning: could not save character replacements: {exc}")

    def add_character_replacement(self, find, replace):
        """Add a find->replace pair. The replacement character is
        automatically registered as a special character if it isn't
        already one. Returns False if find/replace are empty or the
        find pattern is already registered."""
        if not find or not replace:
            return False
        if find in self.find_chars:
            return False

        self.find_chars.append(find)
        self.replace_chars.append(replace)

        if replace not in self.special_chars:
            self.special_chars.append(replace)
            self.script_end_chars = self._build_script_end_chars()
            global special_chars
            special_chars = self.special_chars

        self.save_chars_config()
        return True

    # ---- slots / behavior ----

    @Slot(str, str, str)
    def replace_text(self, text, find, replace):
        """Replace all occurrences of find with replace in text."""
        result = text.replace(find, replace)
        self.textReplaced.emit(result)
        return result

    @Slot(str)
    def get_autofill_suggestions(self, partial_text):
        partial = partial_text.strip().lower()

        suggestions = [
            name
            for name in self.justification_names
            if partial in name.lower()
        ][:20]

        self.suggestionReady.emit(suggestions)
        return suggestions

    @Slot(str)
    def replace_all_special(self, text):
        """Replace all special tokens in text."""
        result = text
        for find, replace in zip(self.find_chars, self.replace_chars):
            result = result.replace(find, replace)
        self.textReplaced.emit(result)
        return result

    def get_font_family(self, ch):
        if '\u0590' <= ch <= '\u05FF':
            return "Arial"

        if ch in self.special_chars:
            return "Cambria Math"

        return "CMU Classical Serif"

    def get_display_runs(self, text):
        """Split text into display runs for font and super/subscript styling."""
        runs = []
        current = ""
        current_font = None
        current_script = None
        script_mode = None

        def flush():
            nonlocal current, current_font, current_script

            if not current:
                return

            runs.append({
                "text": current,
                "font": current_font,
                "script": current_script,
            })

            current = ""

        for ch in text:
            if ch == "^":
                flush()
                script_mode = "super"
                continue

            if ch == "_":
                flush()
                script_mode = "sub"
                continue

            if ch in self.script_end_chars:
                flush()
                script_mode = None

            font = self.get_font_family(ch)

            if current_font is None:
                current_font = font
                current_script = script_mode
                current = ch

            elif font == current_font and current_script == script_mode:
                current += ch

            else:
                flush()
                current_font = font
                current_script = script_mode
                current = ch

        flush()
        return runs

    def get_templates_for_justification(self, justification_name):
        """Find the justification by name and return its templates."""
        for item in self.justifications:
            if justification_name in item.get("name", []):
                return item.get("templates", [])
        return []

    def get_categories_for_justification(self, justification_name):
        """Return the list of categories for a justification, supporting
        both the new multi-category "categories" list field and the
        legacy single "category" string field."""
        for item in self.justifications:
            if justification_name in item.get("name", []):
                categories = item.get("categories")
                if categories is None:
                    categories = item.get("category")
                if isinstance(categories, str):
                    categories = [categories]
                if not isinstance(categories, list):
                    categories = []
                return categories
        return []

    def justification_shape_symbol(self, justification_name):
        """Return the polygon symbol (△ for triangles, ◻ for
        quadrilaterals) matching this justification's categories, or
        None if it doesn't belong to a shape category we recognize."""
        categories = [c.lower() for c in self.get_categories_for_justification(justification_name)]
        for keyword, symbol in SHAPE_SYMBOLS:
            if any(keyword in category for category in categories):
                return symbol
        return None

    def extract_variables(self, template):
        """Extract all {VAR} placeholders from a template string."""
        import re
        matches = re.findall(r'\{([A-Z][A-Z0-9]*)\}', template)
        seen = set()
        result = []
        for var in matches:
            if var not in seen:
                result.append(var)
                seen.add(var)
        return result

    def substitute_template(self, template, variable_values):
        """Replace variables in template with provided values."""
        result = template
        for var, value in variable_values.items():
            result = result.replace(f"{{{var}}}", value)
        return result

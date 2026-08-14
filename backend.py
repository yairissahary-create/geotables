from PySide6.QtCore import QObject, Signal, Slot
import json
from pathlib import Path

special_chars=["≠", "≤", "≥","∦", "∠","∥","⟂","⟂","△","≌","∼"]

# Load justifications.json safely (tolerate missing or malformed files).
SCRIPT_DIR = Path(__file__).resolve().parent


script_end_chars = set([" ", "+", "-", "=", ")", "]", "}", ","] + special_chars)
class Backend(QObject):
    find_chars = ["!=", "<=", ">=","!prl'", "ngl'","prl'","||","prp'","|_","tr'","cng'","~=","=~","sm'","~"]
    replace_chars = ["≠", "≤", "≥","∦", "∠","∥","∥","⟂","⟂","△","≌","≌","≌","∼","∼"]

    textReplaced = Signal(str)
    suggestionReady = Signal(list)

    def __init__(self, data_dir=None):
        super().__init__()
        self.data_dir = Path(data_dir) if data_dir else SCRIPT_DIR
        self.justifications = self._load_justifications()
        names = []
        for item in self.justifications:
            for n in item.get("name", []) if isinstance(item.get("name", []), list) else []:
                names.append(n)
        self.justification_names = names

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
    @Slot(str, str, str)
    def replace_text(self, text, find, replace):
        """Replace all occurrences of find with replace in text."""
        result = text.replace(find, replace)
        self.textReplaced.emit(result)
        return result
    
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

        if ch in special_chars:
            return "STIX Two Math"

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

            if ch in script_end_chars:
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

    def get_templates_for_justification(self, justification_name):
        """Find the justification by name and return its templates."""
        for item in self.justifications:
            if justification_name in item.get("name", []):
                return item.get("templates", [])
        return []

    def extract_variables(self, template):
        """Extract all {VAR} placeholders from a template string."""
        import re
        # Find all {UPPERCASE} or {MixedCase} patterns
        matches = re.findall(r'\{([A-Z][A-Z0-9]*)\}', template)
        # Return unique variables in order of first appearance
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

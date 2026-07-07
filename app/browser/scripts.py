from __future__ import annotations

import json
from typing import Any


def normalize_expression(expression: str) -> str:
    stripped = expression.strip()
    if stripped.startswith("() =>") or stripped.startswith("async () =>"):
        return f"({stripped})()"
    return expression


def selector_exists_script(selector: str) -> str:
    selector_json = json.dumps(selector)
    return f"document.querySelector({selector_json}) !== null"


def click_script(selector: str) -> str:
    selector_json = json.dumps(selector)
    return f"""
(() => {{
  const el = document.querySelector({selector_json});
  if (!el) return false;
  el.scrollIntoView({{block: 'center', inline: 'center'}});
  if (typeof el.focus === 'function') el.focus();
  el.click();
  return true;
}})()
"""


def type_script(selector: str, text: str) -> str:
    selector_json = json.dumps(selector)
    text_json = json.dumps(text)
    return f"""
(() => {{
  const el = document.querySelector({selector_json});
  const text = {text_json};
  if (!el) return false;
  el.scrollIntoView({{block: 'center', inline: 'center'}});
  if (typeof el.focus === 'function') el.focus();
  if ('value' in el) {{
    const current = String(el.value ?? '');
    const start = Number.isInteger(el.selectionStart) ? el.selectionStart : current.length;
    const end = Number.isInteger(el.selectionEnd) ? el.selectionEnd : start;
    el.value = current.slice(0, start) + text + current.slice(end);
    const cursor = start + text.length;
    if (typeof el.setSelectionRange === 'function') el.setSelectionRange(cursor, cursor);
  }} else {{
    el.textContent = String(el.textContent ?? '') + text;
  }}
  el.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: text}}));
  return true;
}})()
"""


def fill_script(selector: str, text: str) -> str:
    selector_json = json.dumps(selector)
    text_json = json.dumps(text)
    return f"""
(() => {{
  const el = document.querySelector({selector_json});
  const text = {text_json};
  if (!el) return false;
  el.scrollIntoView({{block: 'center', inline: 'center'}});
  if (typeof el.focus === 'function') el.focus();
  if ('value' in el) {{
    el.value = text;
    if (typeof el.setSelectionRange === 'function') {{
      const cursor = text.length;
      el.setSelectionRange(cursor, cursor);
    }}
  }} else {{
    el.textContent = text;
  }}
  el.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertReplacementText', data: text}}));
  el.dispatchEvent(new Event('change', {{bubbles: true}}));
  return true;
}})()
"""


def dispatch_file_input_change_script(selector: str) -> str:
    selector_json = json.dumps(selector)
    return f"""
(() => {{
  const el = document.querySelector({selector_json});
  if (!el) return false;
  el.dispatchEvent(new Event('input', {{bubbles: true}}));
  el.dispatchEvent(new Event('change', {{bubbles: true}}));
  return true;
}})()
"""


def local_storage_state_script() -> str:
    return """
(() => {
  try {
    if (!location.origin || location.origin === 'null') return null;
    const localStorageItems = {};
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      localStorageItems[key] = localStorage.getItem(key);
    }
    return {origin: location.origin, localStorage: localStorageItems};
  } catch {
    return null;
  }
})()
"""


def restore_local_storage_script(local_storage: dict[str, Any]) -> str:
    local_storage_json = json.dumps({
        str(key): "" if value is None else str(value)
        for key, value in local_storage.items()
    })
    return f"""
(() => {{
  const items = {local_storage_json};
  localStorage.clear();
  for (const [key, value] of Object.entries(items)) {{
    localStorage.setItem(key, value);
  }}
  return true;
}})()
"""

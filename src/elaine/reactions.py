import re
import emoji

ALLOWED_REACTIONS = {"👍", "❤️", "🤔", "😌", "😤", "🎉", "👀", "✅"}


def chat_text(text):
    """Enforce emoji-free prose, including custom Discord emoji and emoji aliases."""
    text = re.sub(r"<a?:[A-Za-z0-9_]+:\d+>", "", text)
    text = re.sub(r":(?:[a-zA-Z_][a-zA-Z0-9_]*|[+-]1):", "", text)
    return emoji.replace_emoji(text, replace="").strip()

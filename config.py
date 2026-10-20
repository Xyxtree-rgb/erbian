import os
import sys

from dotenv import load_dotenv


def resource_path(relative_path):
    """Return the path to a bundled resource or a project resource."""
    if hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


load_dotenv(resource_path(".env"))

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

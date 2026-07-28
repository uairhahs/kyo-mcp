import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pkg.src.kyo_mcp.database import get_connection

conn = get_connection()
# Check if the table exists and has rows
cur = conn.cursor()
cur.execute("SELECT count(*) FROM knowledge_concepts")
count = cur.fetchone()[0]
print(f"Database is active with {count} nodes.")

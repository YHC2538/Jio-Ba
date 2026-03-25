
import asyncio
from unittest.mock import MagicMock
import sys
import os

# Add current directory to path
sys.path.append(os.getcwd())

# Mock modules
sys.modules['discord'] = MagicMock()
sys.modules['discord.ext'] = MagicMock()
sys.modules['discord.ext.commands'] = MagicMock()

# Set Mock Env
os.environ["GOOGLE_API_KEY"] = "DUMMY_KEY"
os.environ["GOOGLE_API_ENDPOINT"] = "http://mock-endpoint"

try:
    from cogs.graph_agent import create_graph, DinnerState
    print("Import 'cogs.graph_agent' successful.")
except ImportError as e:
    print(f"Import Failed: {e}")
    sys.exit(1)

async def verify():
    print("Verifying Graph Construction...")
    mock_bot = MagicMock()
    
    try:
        graph = create_graph(mock_bot)
        print("Graph created successfully.")
        
        # Test imports of tools
        from cogs.tools.dinner_tools import get_dinner_tools
        tools = get_dinner_tools(mock_bot)
        print(f"Tools Loaded: {[t.name for t in tools]}")
        
        print("Verification Complete: Import and Construction OK.")
        
    except Exception as e:
        print(f"Verification Failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(verify())

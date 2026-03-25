import os
import sys
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from cogs.utils.cost_tracker import track_usage

# Load environment variables
load_dotenv()

def test_connection():
    # 1. Get Config
    api_key = os.getenv("GOOGLE_API_KEY") 
    raw_base_url = os.getenv("GOOGLE_API_ENDPOINT", "https://openrouter.ai/api/v1")
    
    # FIX: OpenAI client appends /chat/completions automatically. We must provide the root.
    base_url = raw_base_url.replace("/chat/completions", "")
    if base_url.endswith("/"):
        base_url = base_url[:-1]
    
    print(f"Checking Environment:")
    print(f"- API Key: {'[Set]' if api_key else '[Missing]'}")
    print(f"- Raw URL: {raw_base_url}")
    print(f"- Client Base URL: {base_url}")
    
    if not api_key:
        print("❌ Error: GOOGLE_API_KEY (used for OpenRouter) is missing!")
        return

    # 2. Initialize ChatOpenAI (The correct client for OpenRouter)
    # Common OpenRouter models: 'google/gemini-2.0-flash-exp:free', 'deepseek/deepseek-chat'
    model_name = "deepseek/deepseek-chat-v3.1" 
    
    print(f"\nInitializing ChatOpenAI with model='{model_name}'...")
    
    try:
        llm = ChatOpenAI(
            model=model_name,
            openai_api_key=api_key,
            base_url=base_url,
            temperature=0.7
        )
        
        print(f"Sending test message to {base_url}...")
        response = llm.invoke([HumanMessage(content="Hello! Are you online?")])
        
        print("\n✅ Connection Successful!")
        print(f"Response: {response.content}")
        
        # Use centralized Cost Tracker
        usage = response.usage_metadata or {}
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        
        cost = track_usage("TEST_CONNECTION", input_tokens, output_tokens, model_name)
        
        print(f"\n💰 Token Usage: In: {input_tokens} | Out: {output_tokens} | Cost: ${cost:.6f} (Logged to data/usage_log.csv)")
        return True
        
    except Exception as e:
        print("\n❌ Connection Failed!")
        print(f"Error: {e}")
        return False

if __name__ == "__main__":
    test_connection()

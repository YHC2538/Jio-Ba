import asyncio
import os
import json
from dotenv import load_dotenv
import google.generativeai as genai
from google.api_core.client_options import ClientOptions

# Load env from .env file
load_dotenv()

def test_search():
    print("=== Testing Gemini Search Grounding (Direct SDK) ===")
    
    api_key = os.getenv("GOOGLE_API_KEY")
    base_url = os.getenv("GOOGLE_API_ENDPOINT")
    
    print(f"API Key: {api_key[:5]}...{api_key[-5:] if api_key else 'None'}")
    print(f"Endpoint: {base_url}")
    
    if not api_key or not base_url:
        print("❌ Error: Missing API Key or Endpoint")
        return

    try:
        # Configure SDK
        genai.configure(
            api_key=api_key,
            client_options=ClientOptions(api_endpoint=base_url),
            transport="rest"
        )
        
        model = genai.GenerativeModel('gemini-3-flash-preview')
        
        # User defined prompt
        search_prompt = """
        請幫我尋找網路資訊 尋找符合的餐廳 以下是條件
        1. 週五晚上有開
        2. 台北市中山區
        3. 日式拉麵店
        4. 價格落於 200-300 間
        5. 人數共五人
        6. 不吃牛肉
        7. 五辛素
        需附上餐廳資訊
        注意事項
        地址
        
        **OUTPUT FORMAT: JSON ONLY**
        [
            {
                "name": "Restaurant Name",
                "address": "Address",
                "rating": "Rating",
                "price_range": "Price",
                "reason": "Why it matches"
            }
        ]
        """
        
        print(f"\nSending Prompt:\n{search_prompt.strip()}\n")
        print("Waiting for response...")
        
        # Invoke with tools
        response = model.generate_content(
            search_prompt,
            tools='google_search_retrieval'
        )
        
        print("\n=== Raw Response ===")
        try:
            print(response.text)
        except Exception as e:
            print(f"Could not get text directly: {e}")
            print(response)

        # Check for grounding metadata
        if response.candidates:
            print("\n=== Grounding Metadata ===")
            print(response.candidates[0].grounding_metadata)
            
    except Exception as e:
        print(f"\n❌ Execution Error: {e}")

if __name__ == "__main__":
    test_search()

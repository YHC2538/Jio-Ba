import os
import csv
from datetime import datetime
from threading import Lock

# Thread-safe writing
_lock = Lock()

def track_usage(event_id: str, input_tokens: int, output_tokens: int, model_name: str = None):
    """
    Calculates cost based on .env rates and appends to data/usage_log.csv.
    Returns the estimated cost.
    """
    if model_name is None:
        model_name = os.getenv("LLM_MODEL_NAME", "deepseek/deepseek-chat")
        
    # Load rates
    try:
        rate_in = float(os.getenv("LLM_COST_INPUT_PER_1M", 0.14))
        rate_out = float(os.getenv("LLM_COST_OUTPUT_PER_1M", 0.28))
    except ValueError:
        rate_in = 0.14
        rate_out = 0.28

    # Calculate Cost
    cost = (input_tokens / 1_000_000) * rate_in + (output_tokens / 1_000_000) * rate_out
    
    # Log File Path
    log_dir = "data"
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "usage_log.csv")
    
    # timestamp
    timestamp = datetime.now().isoformat()
    
    # Write to CSV
    with _lock:
        file_exists = os.path.exists(log_file)
        with open(log_file, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Timestamp", "EventID", "Model", "InputTokens", "OutputTokens", "CostUSD"])
            
            writer.writerow([timestamp, event_id, model_name, input_tokens, output_tokens, f"{cost:.6f}"])
            
    return cost

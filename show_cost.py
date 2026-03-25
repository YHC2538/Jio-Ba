import csv
import os
from collections import defaultdict

def show_total_cost():
    log_file = "data/usage_log.csv"
    if not os.path.exists(log_file):
        print("No usage log found!")
        return

    total_cost = 0.0
    total_input = 0
    total_output = 0
    model_stats = defaultdict(lambda: {"cost": 0.0, "in": 0, "out": 0})

    try:
        with open(log_file, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    cost = float(row["CostUSD"])
                    in_tok = int(row["InputTokens"])
                    out_tok = int(row["OutputTokens"])
                    model = row["Model"]

                    total_cost += cost
                    total_input += in_tok
                    total_output += out_tok
                    
                    model_stats[model]["cost"] += cost
                    model_stats[model]["in"] += in_tok
                    model_stats[model]["out"] += out_tok
                    
                except ValueError:
                    continue
    except Exception as e:
        print(f"Error reading log: {e}")
        return

    print(f"\n💰 === Total Cost Report === 💰")
    print(f"Total Spent: ${total_cost:.6f} USD")
    print(f"Total Tokens: {total_input + total_output:,} (In: {total_input:,} | Out: {total_output:,})")
    print("-" * 40)
    print("By Model:")
    for model, stats in model_stats.items():
        print(f"- {model}: ${stats['cost']:.6f} (In: {stats['in']:,} | Out: {stats['out']:,})")
    print("=" * 40 + "\n")

if __name__ == "__main__":
    show_total_cost()

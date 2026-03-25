
import google.genai.errors
import inspect

print("Classes in google.genai.errors:")
for name, obj in inspect.getmembers(google.genai.errors):
    if inspect.isclass(obj):
        print(f"Class: {name}")
        if hasattr(obj, '_get_message'):
            print(f"  -> Found _get_message in {name}")

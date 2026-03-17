from binance.client import Client
import os

def test_headers():
    api_key = os.getenv("BINANCE_API_KEY", "dummy")
    api_secret = os.getenv("BINANCE_API_SECRET", "dummy")
    client = Client(api_key, api_secret)
    
    print(f"Client type: {type(client)}")
    print(f"Has response: {hasattr(client, 'response')}")
    
    # Even without real credentials, some methods might set the response attribute if they fail after request
    try:
        client.get_exchange_info()
    except Exception as e:
        print(f"Error: {e}")
        
    print(f"Has response after call: {hasattr(client, 'response')}")
    if hasattr(client, 'response'):
        print(f"Response type: {type(client.response)}")
        print(f"Headers: {client.response.headers}")

if __name__ == "__main__":
    test_headers()

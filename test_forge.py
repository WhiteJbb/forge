"""Test script for Forge API"""
import httpx
import asyncio
import json

async def test():
    base_url = "http://localhost:4001"
    
    print("=== Testing /v1/chat/completions ===")
    async with httpx.AsyncClient(timeout=30) as client:
        payload = {
            "model": "coder",
            "messages": [
                {"role": "user", "content": "Say hello in one word"}
            ],
            "max_tokens": 10
        }
        r = await client.post(f"{base_url}/v1/chat/completions", json=payload)
        print(f"Status: {r.status_code}")
        data = r.json()
        print(json.dumps(data, indent=2)[:1000])
        
    print("\n=== Testing /metrics ===")
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{base_url}/metrics")
        print(f"Status: {r.status_code}")
        print(json.dumps(r.json(), indent=2)[:500])

if __name__ == "__main__":
    asyncio.run(test())

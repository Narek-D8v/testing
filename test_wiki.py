import aiohttp
import asyncio
import json

async def test():
    params = {
        'action': 'query',
        'list': 'search',
        'srsearch': 'hello world',
        'format': 'json',
        'srlimit': 3
    }
    async with aiohttp.ClientSession() as s:
        async with s.get('https://en.wikipedia.org/w/api.php', params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get('query', {}).get('search', [])
                for r in results:
                    title = r['title']
                    snippet = r.get('snippet', '')[:60]
                    print(f'Wiki: {title} - {snippet}')
            else:
                print(f'Wiki failed: {resp.status}')

asyncio.run(test())

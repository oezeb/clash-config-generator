'''
Module for scaping and testing http/socks4/socks5 proxy

'''

import asyncio
from datetime import datetime
import re
import shutil
import yaml
from collections import defaultdict
from aiohttp import ClientSession
from aiohttp_socks import ProxyConnector

regex = re.compile(r'\b((?:(?:2(?:[0-4]\d|5[0-5])|[0-1]?\d?\d)\.){3}(?:(?:2(?:[0-4]\d|5[0-5])|[0-1]?\d?\d))):(6(?:5(?:5(?:3[0-6]|[0-2]?\d)|[0-4]?\d?\d)|[0-4]?\d?\d?\d)|[0-5]?\d?\d?\d?\d)\b')

class ProxyScraper:
    @staticmethod
    async def  get_proxies(sources: dict):
        return await ProxyScraper._fetch_all_sources(sources)

    @staticmethod
    async def _fetch_source(session: ClientSession, source: str, proto: str, proxies: dict) -> None:
        """Get proxies from source.

        Args:
            source: Proxy list URL
            proto: http/socks4/socks5.
        """
        source = source.strip()
        try:
            async with session.get(source, timeout=15) as response:
                status = response.status
                text = await response.text()
        except Exception as e:
            msg = f"{source} | Error"
            exc_str = str(e)
            if exc_str and exc_str != source:
                msg += f": {exc_str}"
            print(msg)
        else:
            for e in regex.finditer(text):
                proxies[proto].add(e.group(0))
            msg = f"source {source} | status code {status}"
            print(msg)
        

    @staticmethod
    async def _fetch_all_sources(sources: dict) -> dict:
        proxies = defaultdict(set)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; rv:102.0)"
                + " Gecko/20100101 Firefox/102.0"
            )
        }
        async with ClientSession(headers=headers) as session:
            coroutines = (
                ProxyScraper._fetch_source(session, source, proto, proxies)
                for proto, sources in sources.items()
                for source in sources
            )
            await asyncio.gather(*coroutines)
        return proxies


class ProxyChecker:
    def __init__(self, timeout: float, max_connections: int) -> None:
        self.timeout = timeout
        self.sem = asyncio.Semaphore(max_connections)
    
    async def check_proxies(self, proxies: dict, url = 'https://www.google.com'):
        self.url = url
        working_proxies = defaultdict(dict)
        coroutines = [
            self._check_proxy(proxy, proto, working_proxies)
            for proto, proxies in proxies.items()
            for proxy in proxies
        ]
        await asyncio.gather(*coroutines)
        return working_proxies

    async def _check_proxy(self, proxy: str, proto: str, working_proxies: dict) -> None:
            """Check if proxy is alive."""
            try:
                async with self.sem:
                    proxy_url = f"{proto}://{proxy}"
                    async with ProxyConnector.from_url(proxy_url) as connector:
                        async with ClientSession(connector=connector) as session:
                            async with session.get(self.url, timeout=self.timeout) as test:
                                if test.status == 200:
                                    async with session.get(
                                        "http://ip-api.com/json/?fields=8217",
                                        timeout=self.timeout,
                                        raise_for_status=True,
                                    ) as response:
                                        working_proxies[proto][proxy] = await response.json()
            except Exception as e:
                # Too many open files
                if isinstance(e, OSError) and e.errno == 24:
                    print("Please, set MAX_CONNECTIONS to lower value.")

async def main():
    # if len(sys.argv) == 1 or sys.argv[1] == '-h' or sys.argv[1] == '--help':
    #     print(__doc__)
    # else:
    cfg = yaml.safe_load(open("config.yaml"))
    clash_cfg = yaml.safe_load(open("clash-config.yaml"))

    proxies = await ProxyScraper.get_proxies(cfg['sources'])
    if clash_cfg['proxies']:
        for proxy in clash_cfg['proxies']:
            proxies[proxy['type']].add(f"{proxy['server']}:{proxy['port']}")
    proxies = await ProxyChecker(cfg['timeout'], cfg['max-connections']).check_proxies(proxies)

   
    clash_cfg['proxies'] = []
    clash_cfg['proxy-groups'][0]['proxies'] = []
    count = defaultdict(int)
    for proto in proxies.keys():
        for key, value in proxies[proto].items():
            key = regex.match(key)
            name = f"{value['country']}-{count[value['country']]}"
            clash_cfg['proxies'].append({
                'name': name,
                'type': proto,
                'server': key.group(1),
                'port': key.group(2)
            })
            clash_cfg['proxy-groups'][0]['proxies'].append(name)
            count[value['country']] += 1

    yaml.safe_dump(clash_cfg, open('clash-config.yaml', 'w'))
    shutil.copy('clash-config.yaml', f"archives/clash-{str(datetime.now()).replace(':','.')}.yaml")


if __name__ == '__main__':
    asyncio.run(main())
'''
Module for scaping and testing http/socks4/socks5 proxy
'''

from argparse import ArgumentParser
from contextlib import redirect_stdout
from aiohttp_socks import ProxyConnector
from collections import defaultdict
from aiohttp import ClientSession
from datetime import datetime
from waitress import serve as waitress_serve
from flask import Flask
import asyncio
import yaml
import sys
import os
import re

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
        
        print(f'[Fetching source]: {source}, {proto}')
        try:
            async with session.get(source, timeout=15) as response:
                text = await response.text()
        except Exception as e:
            
            print(f"[Error]: {e}")
        else:
            res = set([e.group(0) for e in regex.finditer(text)])
            
            print(f"[Success]: {source}, proxy count={len(res)}")
            proxies[proto] = res.union(proxies[proto])

    @staticmethod
    async def _fetch_all_sources(sources: dict) -> dict:
        
        print(f"[Fetching proxies from sources]: {','.join(['='.join((k, str(len(v)))) for k, v in sources.items()])}")
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
        
        print(f"""[Checking all scraped Proxies]: {','.join(['='.join((k, str(len(v)))) for k, v in proxies.items()])}""")
        print(f"[Checking all scraped Proxies]: testing url={url}")
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
            
            print(f"[Checking proxy]: {proxy}, {proto}")
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
                                        
                                        print(f"[Checking all scraped Proxies]: {proxy} working")
                                        print(str(working_proxies[proto][proxy]))
            except Exception as e:

                print(f"[Error]: {e}")
                # Too many open files
                if isinstance(e, OSError) and e.errno == 24:
                    print("Please, set MAX_CONNECTIONS to lower value.")


def serve():
    # create and configure the app
    app = Flask(__name__, instance_relative_config=True)
    app.secret_key = '346be02eedc7f65e81d184376afe549da06ba7467033a82a43c4522fd36f2216'

    # ensure the archives folder exists
    try:
        os.makedirs('archives')
    except OSError:
        pass

    # create routes
    @app.route('/')
    def index():
        return f'''
        <a href="/clash-config.yaml">clash-config.yaml</a><br/>
        <a href="/archives">archives</a><br/>
        '''

    @app.route('/clash-config.yaml')
    def clash_config():
        return f"<pre>{open('clash-config.yaml').read()}</pre>"

    @app.route('/archives')
    @app.route('/archives/<filename>')
    def archives(filename = None):
        if not filename:
            return ''.join([f"<a href='archives/{e}' >{e}</a><br/>" for e in os.listdir('archives')])
        else:
            return f"<pre>{open(f'archives/{filename}').read()}</pre>"

    waitress_serve(app, host='0.0.0.0', port=8080)

async def gen_config():
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

    if not os.path.exists('archives/'):
        os.makedirs('archives/')

    now = datetime.now()

    content = yaml.safe_dump(clash_cfg)
    with open('clash-config.yaml', 'w') as f:
        f.write(content)
    with open(f"archives/clash-config-{now.strftime('%Y-%m-%d %H.%M.%S')}.yaml", 'w') as f:
        f.write(content)
    
    print(content)

async def main():
    if len(sys.argv) == 1:
        await gen_config()
    else:
        parser = ArgumentParser(description=__doc__)
        parser.add_argument('--serve', action='store_const', default=False, const=serve)
        parser.add_argument('--gen-config', action='store_const', default=False, const=gen_config)
        args = parser . parse_args ()
        if args.gen_config:
            await args.gen_config()
        if args.serve:
            args.serve()
        
if __name__ == '__main__':
    asyncio.run(main())
"""Runtime mechanics only: original trading signal/exit parameters are unchanged.
All amounts are paper budgets; no guarantee against gaps or unavailable liquidity.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
import json,math,os,tempfile,threading,time

VERSION='RUNTIME_DURABLE_PAPER_V2'

def _number(value):
    x=float(value)
    if not math.isfinite(x):raise ValueError('non-finite budget')
    return x

def plan_notional(requested,available,day_limit,day_pnl,stop_pct,buffer_pct,fixed_cost):
    """Plan paper notional without resetting accumulated PnL.
    day_limit=0 disables the daily cap while cash availability still limits size.
    A positive day_limit preserves bounded daily-risk sizing.
    """
    try:
        requested,available,day_limit,day_pnl,stop_pct,buffer_pct,fixed_cost=map(_number,(requested,available,day_limit,day_pnl,stop_pct,buffer_pct,fixed_cost))
        if requested<=0 or available<=0 or day_limit<0 or stop_pct<=0 or buffer_pct<0 or fixed_cost<0:return 0.0
        cash=max(0.,available-fixed_cost)
        if day_limit == 0:
            amount=min(requested,cash)
        else:
            remaining=max(0.,day_limit+day_pnl)
            risk=max(0.,remaining-fixed_cost)/((stop_pct+buffer_pct)/100)
            amount=min(requested,cash,risk)
        return float(Decimal(str(amount)).quantize(Decimal('.01'),rounding=ROUND_DOWN))
    except (ValueError,TypeError,OverflowError):return 0.0

def atomic_json(path,data):
    """Replace an entire complete state. Interrupted writes retain the old file."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    text=json.dumps(data,ensure_ascii=False,allow_nan=False)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        if path.exists() and hasattr(os,'fchmod'):os.fchmod(fd,path.stat().st_mode & 0o777)
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            f.write(text);f.flush();os.fsync(f.fileno())
        # Windows readers can temporarily deny replacement of an open file.
        # Retry only permission/share races; disk/full/schema failures still fail.
        for attempt in range(6):
            try:
                os.replace(name,path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(.01*(attempt+1))
        if os.name == 'posix':
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
    finally:
        try:os.unlink(name)
        except FileNotFoundError:pass

class DiscoveryCache:
    """Network discovery is independent of price/position refresh.
    A timed-out profile endpoint must not suspend processing already known pools.
    Expired discovery is never retained indefinitely.
    """
    def __init__(self,loader,clock=time.monotonic,refresh_seconds=30,max_age_seconds=300):
        self.loader=loader;self.clock=clock;self.refresh=refresh_seconds;self.max_age=max_age_seconds
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='neo-discovery')
        self.lock=threading.Lock();self.future=None;self.data=([],{});self.updated=None;self.attempt=None
    def get(self):
        with self.lock:
            now=self.clock()
            if self.future is not None and self.future.done():
                try:
                    result=self.future.result()
                    if isinstance(result,tuple) and len(result)==2 and isinstance(result[0],list) and result[0] and isinstance(result[1],dict):
                        self.data=(list(result[0]),dict(result[1]));self.updated=now
                except Exception:pass
                self.future=None
            if self.future is None and (self.attempt is None or now-self.attempt>=self.refresh):
                self.attempt=now;self.future=self.pool.submit(self.loader)
            if self.updated is None or now-self.updated>self.max_age:return [],{}
            return list(self.data[0]),dict(self.data[1])
    def stop(self):self.pool.shutdown(wait=False,cancel_futures=True)

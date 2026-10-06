import LabPairedPanel, { type LabPairedSnapshot } from './components/LabPairedPanel';
import PaperTrainingPanel, { type PaperTrainingSnapshot } from './components/PaperTrainingPanel';
import AstraBrainPanel, { type AstraSnapshot } from './components/AstraBrainPanel';
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity, Bot, ChevronRight, CircleDollarSign, Clock3, ExternalLink,
  Flame, Gauge, Pause, Play, RefreshCw, Search, ShieldCheck, Sparkles,
  TrendingDown, TrendingUp, WalletCards, Zap,
} from 'lucide-react';
import {
  Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { supabase } from './lib/supabase';
import {
  backendErrorMessage, dashboardConnectionStatus, initialDashboardConnection,
  moneyOrUnavailable, paperApiConfiguration, percentageOrUnavailable, readAccountStateCache, writeAccountStateCache,
} from './lib/paperDashboardState';

const apiConfiguration = paperApiConfiguration((import.meta as any).env.VITE_NEO_API_URL, (import.meta as any).env.DEV);
const API = apiConfiguration.url;

type Signal = { kind: 'positive' | 'neutral' | 'risk'; title: string; detail: string };
type TxWindow = { buys: number; sells: number };
type Coin = {
  address: string; pairAddress: string; dexId: string; dexUrl: string;
  name: string; symbol: string; imageUrl: string; description: string;
  priceUsd: number; marketCap: number; fdv: number; liquidityUsd: number;
  volume: Record<'m5' | 'h1' | 'h6' | 'h24', number>;
  priceChange: Record<'m5' | 'h1' | 'h6' | 'h24', number>;
  txns: Record<'m5' | 'h1' | 'h6' | 'h24', TxWindow>;
  ageMinutes: number | null; sources: string[]; boostAmount: number;
  score: number; riskScore: number; posture: 'SETUP' | 'WATCH' | 'WAIT' | 'SKIP';
  signals: Signal[]; updatedAt: number;
};
type Position = {
  id: string; address: string; pairAddress: string; name: string; symbol: string;
  imageUrl: string; entry_price: number; current_price: number; peak_price: number;
  execution_entry_price?: number; execution_exit_price?: number; execution_verification_version?: string;
  notional_usd: number; score: number; current_score: number; opened_at: number;
  updated_at: number; pnl_pct: number; pnl_usd: number;
  why_entry: string[]; risks_at_entry: string[]; closed_at?: number;
  exit_price?: number; exit_reason?: string; trade_no?: number; session_id?: string;
  quantity?: number; balance_at_entry?: number; balance_before?: number; balance_after?: number;
  available_before_entry?: number; available_after_entry?: number; dex_url?: string;
  entry_liquidity_usd?: number; entry_volume_h1?: number; entry_market_cap?: number; entry_change_m5?: number;
  exit_liquidity_usd?: number; exit_volume_h1?: number; exit_market_cap?: number; exit_change_m5?: number;
  strategy_id?: string; strategy_matches?: string[];
};
type PricePoint = { ts: number; price: number; liquidity: number; volumeH1: number; score: number };
type LabTrade = {
  trade_no?: number; strategy_id?: string; symbol: string; name?: string; address: string; pairAddress?: string;
  entry_price?: number; execution_entry_price?: number; exit_price?: number; execution_exit_price?: number;
  notional_usd: number; opened_at: number; closed_at?: number; score?: number; pnl_pct?: number; pnl_usd?: number;
  balance_before?: number; balance_after?: number; exit_reason?: string; execution_mode?: string;
  entry_dex_fee_usd?: number; exit_dex_fee_usd?: number; entry_network_fee_usd?: number; exit_network_fee_usd?: number;
  entry_price_impact_pct?: number; exit_price_impact_pct?: number; entry_slippage_pct?: number; exit_slippage_pct?: number;
};
type LabPosition = {
  symbol: string; address: string; pairAddress?: string; strategy_id: string; opened_at: number; pnl_pct: number;
  open_pnl_usd?: number; notional_usd: number; entry_price?: number; execution_entry_price?: number;
  current_price?: number; entry_dex_fee_usd?: number; entry_network_fee_usd?: number;
  estimated_exit_fee_usd?: number; estimated_exit_impact_pct?: number; execution_mode?: string;
  updated_at?: number; mark_received_at?: number; mark_source?: string; quote_status?: string;
  quote_age_ms?: number; quote_unavailable_reason?: string;
};
type LegacyLabBook = { id: string; strategy_id: string; name: string; starting_balance: number; balance: number; position: LabPosition | null; history: LabTrade[] };
type LabBook = { id: string; name: string; starting_balance: number; balance: number; portfolio_group?: 'PROMOTED_PAPER' | 'PROMOTION_DRAINING' | 'TEST'; promotion_pending?: boolean; allocation_usd?: number; max_position_fraction?: number; position: LabPosition | null; history: LabTrade[] };
type LabStats = { trades: number; wins: number; losses: number; win_rate: number; profit_factor: number | null; realized_pnl: number; equity: number; return_pct: number; open: boolean; valuation_stale?: boolean; mark_age_ms?: number };
type StrategyLab = { paired?: LabPairedSnapshot; astra?: AstraSnapshot; execution_basis?: string; execution_note?: string; portfolio_setup?: { version?: string; status?: string; total_allocated_capital_usd?: number; allocation_per_strategy_usd?: number; max_position_fraction?: number; strategies?: string[]; historical_simulations?: number; unique_market_episodes_30m?: number; legacy_draining_books?: Record<string, LegacyLabBook>; legacy_open_position_count?: number; legacy_open_positions?: string[]; evidence_note?: string; promotion_error?: string }; status: string; updated_at: number; started_at: number; books: Record<string, LabBook>; stats: Record<string, LabStats>; error?: string };
type LiveTrade = { ts: number; direction: 'BUY' | 'SELL'; token_amount: number; usd_amount: number; wallet: string; note: string; address: string; pairAddress: string; symbol: string; signature: string; slot: number };
type FlowStats = { seconds: number; trades: number; buys: number; sells: number; buy_usd: number; sell_usd: number; buy_sell_usd_ratio: number; unique_wallets: number; max_buy_usd: number; max_sell_usd: number };
type TapeStatus = { status?: string; tracked_pairs?: number; updated_at?: number; source?: string; error?: string | null };
type MonitorState = {
  running: boolean; status: string; message: string; last_scan_at: number; scan_count: number;
  feed: Coin[]; positions: Position[]; history: Position[];
  events: { ts: number; text: string }[];
  source_status: Record<string, string>;
  live_tape: LiveTrade[]; live_tape_status: TapeStatus;
  strategy_lab: StrategyLab;
  paper_training?: PaperTrainingSnapshot;
  stats: { feed_count: number; open_positions: number; closed_trades: number; wins: number; win_rate: number; realized_today_usd: number; demo_starting_balance_usd: number; demo_balance_usd: number; demo_equity_usd: number; demo_available_usd: number; demo_reserved_usd: number; unrealized_pnl_usd: number; realized_total_usd: number; return_pct: number; demo_started_at: number; demo_session_id: string };
  config: { signal_strategy?: string; ensemble_strategies?: string[]; risk_overlay?: string; execution_verification_version?: string; scan_seconds: number; position_scan_seconds?: number; entry_score: number; max_positions: number; stop_loss_pct: number; take_profit_pct: number; trailing_pct: number; max_hold_minutes: number; min_liquidity_usd: number; trade_notional_usd: number; max_daily_loss_usd: number; starting_balance_usd: number };
};
type TokenDetail = { coin: Coin; history: PricePoint[]; position: Position | null; trades: Position[]; live_tape?: LiveTrade[]; flow?: FlowStats };
type Filter = 'ALL' | 'SETUP' | 'WATCH' | 'NEW' | 'BOOSTED';
function isMonitorState(value: unknown): value is MonitorState {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as MonitorState;
  return Array.isArray(candidate.feed) && Array.isArray(candidate.positions) && Array.isArray(candidate.history)
    && Array.isArray(candidate.events) && typeof candidate.running === 'boolean' && typeof candidate.status === 'string'
    && !!candidate.stats && !!candidate.config
    && ['demo_balance_usd', 'demo_equity_usd', 'demo_available_usd', 'demo_reserved_usd', 'return_pct',
      'demo_starting_balance_usd', 'realized_today_usd', 'unrealized_pnl_usd', 'closed_trades', 'win_rate',
      'open_positions', 'feed_count'].every(key => typeof candidate.stats[key] === 'number' && Number.isFinite(candidate.stats[key]));
}
const fmtMoney = (value = 0) => value >= 1_000_000 ? `$${(value / 1_000_000).toFixed(2)}M` : value >= 1_000 ? `$${(value / 1_000).toFixed(1)}K` : `$${value.toFixed(0)}`;
const fmtPrice = (value = 0) => value >= 1 ? `$${value.toFixed(4)}` : value >= 0.01 ? `$${value.toFixed(6)}` : value >= 0.0001 ? `$${value.toFixed(8)}` : `$${value.toPrecision(5)}`;
const ageLabel = (minutes: number | null) => minutes == null ? '—' : minutes < 60 ? `${Math.round(minutes)}m` : minutes < 1440 ? `${(minutes / 60).toFixed(1)}h` : `${(minutes / 1440).toFixed(1)}d`;
const shortAddress = (value: string) => `${value.slice(0, 4)}…${value.slice(-4)}`;
const timeLabel = (stamp: number) => stamp ? new Date(stamp).toLocaleTimeString('bg-BG', { hour: '2-digit', minute: '2-digit' }) : '—';
const tapeTimeLabel = (stamp: number) => stamp ? new Date(stamp).toLocaleTimeString('bg-BG', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';
const fullTimeLabel = (stamp: number) => stamp ? new Date(stamp).toLocaleString('bg-BG', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';
const durationLabel = (start?: number, end?: number) => !start || !end ? '—' : `${Math.max(0, Math.round((end - start) / 60000))}m`;
const strategyName: Record<string, string> = { EARLY: 'Early Runner', MOMENTUM: 'Momentum', PRECISION: 'Precision', ULTRA_PRECISION: 'Ultra Precision' };
const strategyLabels = (matches?: string[]) => Array.isArray(matches) ? matches.map(id => strategyName[id] || id).join(' · ') : '';
function ScoreBadge({ coin }: { coin: Coin }) {
  const cls = coin.posture === 'SETUP' ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-300' : coin.posture === 'WATCH' ? 'border-sky-400/20 bg-sky-400/10 text-sky-300' : coin.posture === 'WAIT' ? 'border-amber-400/20 bg-amber-400/10 text-amber-200' : 'border-red-400/20 bg-red-400/10 text-red-300';
  return <div className={`rounded-lg border px-2 py-1 text-[10px] font-black ${cls}`}>{coin.score.toFixed(0)} · {coin.posture}</div>;
}

function Change({ value, compact = false }: { value: number; compact?: boolean }) {
  const positive = value >= 0;
  return <span className={`inline-flex items-center gap-1 font-black ${compact ? 'text-[10px]' : 'text-sm'} ${positive ? 'text-emerald-300' : 'text-red-300'}`}>
    {positive ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}{positive ? '+' : ''}{value.toFixed(1)}%
  </span>;
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return <div className="rounded-2xl border border-white/[0.07] bg-white/[0.025] p-3.5">
    <div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">{label}</div>
    <div className="mt-1 text-lg font-black text-white">{value}</div>
    {hint && <div className="mt-1 text-[9px] text-slate-600">{hint}</div>}
  </div>;
}

function CoinAvatar({ coin, size = 'md' }: { coin: Coin; size?: 'sm' | 'md' | 'lg' }) {
  const dimensions = size === 'lg' ? 'h-14 w-14' : size === 'sm' ? 'h-8 w-8' : 'h-10 w-10';
  return coin.imageUrl ? <img src={coin.imageUrl} alt="" className={`${dimensions} shrink-0 rounded-xl border border-white/10 object-cover`} /> : <div className={`${dimensions} flex shrink-0 items-center justify-center rounded-xl border border-white/10 bg-white/[0.04] text-xs font-black text-emerald-300`}>{coin.symbol.slice(0, 2)}</div>;
}
export default function App() {
  const [state, setState] = useState<MonitorState | null>(null);
  const [error, setError] = useState('');
  const [selectedAddress, setSelectedAddress] = useState('');
  const [selectedPaperStrategy, setSelectedPaperStrategy] = useState('');
  const [selectedLabStrategyId, setSelectedLabStrategyId] = useState('');
  const [detail, setDetail] = useState<TokenDetail | null>(null);
  const [filter, setFilter] = useState<Filter>('ALL');
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const [connection, setConnection] = useState(initialDashboardConnection);
  const [now, setNow] = useState(Date.now);
  const cacheChecked = useRef(false);
  const stateGeneration = useRef(0);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);


  useEffect(() => {
    let cancelled = false;
    let retryTimer: number | undefined;
    let pollTimer: number | undefined;
    let inFlight = false;
    let activeController: AbortController | undefined;
    const generation = ++stateGeneration.current;

    const schedulePoll = () => {
      if (cancelled) return;
      pollTimer = window.setTimeout(() => void load(0), 2000);
    };

    const load = async (attempt = 0) => {
      if (cancelled || inFlight) return;
      inFlight = true;
      let retryScheduled = false;
      try {
        if (apiConfiguration.error) throw new Error(apiConfiguration.error);
        if (!supabase) throw new Error('Supabase unavailable');
        const { data: { session }, error: authError } = await supabase.auth.getSession();
        if (authError) throw authError;
        const token = session?.access_token;
        const userId = session?.user.id;
        if (!token || !userId) throw new Error('Session expired');
        if (cancelled || generation !== stateGeneration.current) return;
        if (!cacheChecked.current) {
          cacheChecked.current = true;
          const cached = readAccountStateCache(sessionStorage, userId, Date.now(), isMonitorState);
          if (cached) {
            setState(cached.state);
            setConnection({ source: 'cache', receivedAt: cached.savedAt, failure: '' });
          }
        }

        const controller = new AbortController();
        activeController = controller;
        const timeout = window.setTimeout(() => controller.abort(), 4000);
        const response = await fetch(`${API}/user/state`, {
          cache: 'no-store',
          signal: controller.signal,
          headers: { Authorization: `Bearer ${token}` },
        }).finally(() => window.clearTimeout(timeout));
        if (!response.ok) throw new Error(`Backend HTTP ${response.status}`);
        const next: unknown = await response.json();
        if (!isMonitorState(next)) throw new Error('Backend върна невалидни данни за PAPER сметката.');
        if (cancelled || generation !== stateGeneration.current) return;
        const receivedAt = Date.now();
        setState(next);
        setConnection({ source: 'network', receivedAt, failure: '' });
        setNow(receivedAt);
        setError('');
        writeAccountStateCache(sessionStorage, userId, next, receivedAt);
        setSelectedAddress(current => current || next.feed[0]?.address || '');
      } catch (err) {
        if (cancelled || generation !== stateGeneration.current) return;
        const message = backendErrorMessage(err);
        setError(message);
        setConnection(current => ({ ...current, failure: message }));
        if (attempt < 3) {
          const delays = [100, 250, 500];
          retryScheduled = true;
          retryTimer = window.setTimeout(() => void load(attempt + 1), delays[attempt]);
        }
      } finally {
        inFlight = false;
        if (!retryScheduled) schedulePoll();
      }
    };

    void load();
    return () => {
      cancelled = true;
      activeController?.abort();
      if (retryTimer) window.clearTimeout(retryTimer);
      if (pollTimer) window.clearTimeout(pollTimer);
    };
  }, [refreshTick]);

  useEffect(() => {
    if (!selectedAddress) return;
    let cancelled = false;
    const loadToken = async () => {
      const generation = stateGeneration.current;
      try {
        if (!supabase || apiConfiguration.error) return;
        const { data: { session } } = await supabase.auth.getSession();
        const token = session?.access_token;
        if (!token) return;
        const response = await fetch(`${API}/user/token?address=${encodeURIComponent(selectedAddress)}`, {
          cache: 'no-store',
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!response.ok) return;
        const next = await response.json() as TokenDetail;
        if (!cancelled && generation === stateGeneration.current) setDetail(next);
      } catch { /* feed still works without token history */ }
    };
    void loadToken();
    const timer = window.setInterval(loadToken, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [selectedAddress]);
  const selectedCoin = useMemo(() => state?.feed.find(c => c.address === selectedAddress) || detail?.coin || state?.feed[0] || null, [state, selectedAddress, detail]);
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (state?.feed || []).filter(coin => {
      const matchesSearch = !q || coin.symbol.toLowerCase().includes(q) || coin.name.toLowerCase().includes(q) || coin.address.toLowerCase().includes(q);
      const matchesFilter = filter === 'ALL' || filter === coin.posture || (filter === 'NEW' && (coin.ageMinutes ?? 999999) < 60) || (filter === 'BOOSTED' && coin.sources.some(s => s.includes('boost')));
      return matchesSearch && matchesFilter;
    });
  }, [state, filter, search]);

  const chartData = useMemo(() => (detail?.history || []).map(p => ({ ...p, label: timeLabel(p.ts) })), [detail]);
  const setupCount = state?.feed.filter(c => c.posture === 'SETUP').length || 0;
  const watchCount = state?.feed.filter(c => c.posture === 'WATCH').length || 0;

  const refreshDashboard = () => {
    setBusy(true);
    setRefreshTick(value => value + 1);
    window.setTimeout(() => setBusy(false), 500);
  };

  const resetMyDemo = async () => {
    if (!supabase) return;
    try {
      if (apiConfiguration.error) throw new Error(apiConfiguration.error);
      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;
      const userId = session?.user.id;
      if (!token || !userId) throw new Error('Session expired');

      const response = await fetch(`${API}/user/reset`, {
        method: 'POST',
        cache: 'no-store',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error(`Backend HTTP ${response.status}`);
      const next: unknown = await response.json();
      if (!isMonitorState(next)) throw new Error('Backend върна невалидни данни след reset.');
      // A state request started before reset must never resurrect the old account snapshot.
      stateGeneration.current += 1;
      const receivedAt = Date.now();
      writeAccountStateCache(sessionStorage, userId, next, receivedAt);
      setState(next);
      setConnection({ source: 'network', receivedAt, failure: '' });
      setNow(receivedAt);
      setDetail(null);
      setError('');
      setRefreshTick(value => value + 1);
    } catch (err) {
      setError(backendErrorMessage(err));
    }
  };

  const dexEmbed = selectedCoin?.pairAddress ? `https://dexscreener.com/solana/${selectedCoin.pairAddress}?embed=1&theme=dark&trades=0&info=0` : '';
  const connectionStatus = dashboardConnectionStatus(connection, now);
  const connected = connectionStatus === 'ONLINE';
  const connectionLabel = connected ? 'BACKEND ONLINE' : connectionStatus;
  const tapeOnline = connected && state?.live_tape_status?.status === 'online';
  const selectedFlowSupported = selectedCoin?.dexId?.toLowerCase() === 'pumpswap';
  const flowStatusLabel = !connected ? connectionLabel
    : !selectedFlowSupported ? `FLOW NOT VERIFIED · ${selectedCoin?.dexId?.toUpperCase() || 'UNKNOWN DEX'}`
      : tapeOnline ? `VERIFIED ON-CHAIN · ${state?.live_tape_status?.tracked_pairs ?? 0} PUMPSWAP POOLS`
        : 'ON-CHAIN COVERAGE INCOMPLETE · NEW ENTRIES STAY BLOCKED';
  const snapshotHint = connection.receivedAt ? `Последен получен отговор: ${fullTimeLabel(connection.receivedAt)}.` : 'PAPER сметката още не е заредена.';
  const emptyStateMessage = state ? 'Няма записи в получения отговор.' : 'Данните още не са заредени от backend.';
  const selectedPaperTrades = selectedPaperStrategy && state ? [
    ...state.positions.filter(trade => trade.strategy_matches?.includes(selectedPaperStrategy)).map(trade => ({ trade, closed: false })),
    ...state.history.filter(trade => trade.strategy_matches?.includes(selectedPaperStrategy)).map(trade => ({ trade, closed: true })),
  ].sort((left, right) => right.trade.opened_at - left.trade.opened_at).slice(0, 20) : [];

  return <div className="min-h-screen bg-[#07090b] text-slate-200">
    <header className="sticky top-0 z-50 border-b border-white/[0.07] bg-[#07090b]/95 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1800px] items-center justify-between gap-4 px-4 py-3 sm:px-6 lg:px-8">        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-emerald-400/20 bg-emerald-400/10"><Zap className="h-5 w-5 text-emerald-300" /></div>
          <div>
            <div className="flex items-center gap-2"><span className="text-base font-black text-white">NEO Meme Coins</span><span className="rounded-md border border-emerald-400/20 bg-emerald-400/10 px-1.5 py-0.5 text-[8px] font-black tracking-[0.14em] text-emerald-300">PAPER</span></div>
            <div className="text-[10px] text-slate-600">Live Solana market intelligence</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div data-testid="backend-connection" className={`hidden items-center gap-2 rounded-xl border px-3 py-2 text-[10px] font-black sm:flex ${connected ? 'border-emerald-400/20 bg-emerald-400/[0.06] text-emerald-300' : 'border-amber-400/20 bg-amber-400/[0.06] text-amber-200'}`}><span className={`h-2 w-2 rounded-full ${connected ? 'animate-pulse bg-emerald-300' : 'bg-amber-300'}`} />{connectionLabel}</div>
          <button onClick={refreshDashboard} disabled={busy} className="flex h-10 items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3 text-[10px] font-black text-white hover:bg-white/[0.06] disabled:opacity-40"><RefreshCw className={`h-3.5 w-3.5 ${busy ? 'animate-spin' : ''}`} /> ОБНОВИ</button>
        </div>
      </div>
    </header>

    <main className="mx-auto max-w-[1800px] px-4 py-5 sm:px-6 lg:px-8">
      {error && <div role="alert" className="mb-4 rounded-2xl border border-red-500/20 bg-red-500/[0.06] px-4 py-3 text-xs text-red-200">{error} {state && 'Показаните данни са от последния отговор и не потвърждават текущото състояние.'} {snapshotHint}</div>}
      {!connected && !error && <div role="status" className="mb-4 rounded-2xl border border-amber-400/20 bg-amber-400/[0.06] px-4 py-3 text-xs text-amber-100">{state ? 'Показани са последно получени данни. Текущите баланси, позиции и работата на бота още не са потвърдени.' : 'Свързване с backend. Балансите и сделките ще се покажат след получаване на данни.'} {snapshotHint}</div>}
      <section className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <Metric label="Demo balance" value={moneyOrUnavailable(state?.stats.demo_balance_usd)} hint={state ? `start ${moneyOrUnavailable(state.stats.demo_starting_balance_usd, false, 0)}` : 'Очакват се данни'} />
        <Metric label="Equity" value={moneyOrUnavailable(state?.stats.demo_equity_usd)} hint={state ? `${percentageOrUnavailable(state.stats.return_pct)} session` : 'Очакват се данни'} />
        <Metric label="Available" value={moneyOrUnavailable(state?.stats.demo_available_usd)} hint={state ? `reserved ${moneyOrUnavailable(state.stats.demo_reserved_usd, false, 0)}` : 'Очакват се данни'} />
        <Metric label="Днес PnL" value={moneyOrUnavailable(state?.stats.realized_today_usd, true)} hint={state ? `unrealized ${moneyOrUnavailable(state.stats.unrealized_pnl_usd, true)}` : 'Очакват се данни'} />
        <Metric label="Open paper" value={state ? `${state.stats.open_positions}/${state.config.max_positions}` : '—'} hint={state ? `${moneyOrUnavailable(state.config.trade_notional_usd, false, 0)} на позиция` : 'Очакват се данни'} />
        <Metric label="Win rate" value={state?.stats.closed_trades ? `${state.stats.win_rate.toFixed(0)}%` : '—'} hint={state ? `${state.stats.closed_trades} затворени` : 'Очакват се данни'} />
        <Metric label="Live coins" value={state ? String(state.stats.feed_count) : '—'} hint={state ? `scan на ${state.config.scan_seconds}s` : 'Очакват се данни'} />
        <Metric label="SETUP" value={state ? String(setupCount) : '—'} hint={state ? `${watchCount} WATCH` : 'Очакват се данни'} />
      </section>
      <section className="mt-4 grid gap-4 xl:grid-cols-[390px_minmax(0,1fr)_340px]">
        <div className="overflow-hidden rounded-3xl border border-white/10 bg-[#0b0e11]">
          <div className="border-b border-white/[0.07] p-4">
            <div className="flex items-center justify-between gap-3">
              <div><div className="text-[9px] font-black uppercase tracking-[0.18em] text-emerald-300">Live Market Feed</div><h2 className="mt-1 text-lg font-black text-white">Ботът избира сам</h2></div>
              <Flame className="h-5 w-5 text-emerald-300" />
            </div>
            <div className="relative mt-3"><Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-600" /><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Coin, symbol или address…" className="h-10 w-full rounded-xl border border-white/10 bg-black/20 pl-9 pr-3 text-xs text-white outline-none placeholder:text-slate-700 focus:border-emerald-400/30" /></div>
            <div className="mt-3 flex gap-1.5 overflow-x-auto pb-1">
              {(['ALL','SETUP','WATCH','NEW','BOOSTED'] as Filter[]).map(item => <button key={item} onClick={() => setFilter(item)} className={`shrink-0 rounded-lg border px-2.5 py-1.5 text-[9px] font-black ${filter === item ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-300' : 'border-white/[0.07] bg-white/[0.02] text-slate-500 hover:text-white'}`}>{item}</button>)}
            </div>
          </div>
          <div className="max-h-[760px] overflow-y-auto p-2">
            {filtered.length === 0 && <div className="p-6 text-center text-xs text-slate-600">{state ? 'Няма coins за този филтър.' : emptyStateMessage}</div>}
            {filtered.map((coin, index) => {
              const active = selectedCoin?.address === coin.address;
              return <button key={coin.address} onClick={() => setSelectedAddress(coin.address)} className={`mb-1.5 w-full rounded-2xl border p-3 text-left transition ${active ? 'border-emerald-400/25 bg-emerald-400/[0.055]' : 'border-white/[0.06] bg-white/[0.015] hover:border-white/10 hover:bg-white/[0.03]'}`}>
                <div className="flex items-center gap-3">
                  <div className="w-5 shrink-0 text-center text-[9px] font-black text-slate-700">#{index + 1}</div><CoinAvatar coin={coin} />
                  <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="truncate text-xs font-black text-white">${coin.symbol}</span>{coin.sources.some(s => s.includes('boost')) && <Sparkles className="h-3 w-3 shrink-0 text-amber-300" />}</div><div className="mt-0.5 truncate text-[9px] text-slate-600">{coin.name} · {ageLabel(coin.ageMinutes)}</div></div>
                  <ScoreBadge coin={coin} />
                </div>
                <div className="mt-3 grid grid-cols-4 gap-2 text-[9px]"><div><div className="text-slate-700">PRICE</div><div className="mt-0.5 font-bold text-white">{fmtPrice(coin.priceUsd)}</div></div><div><div className="text-slate-700">5M</div><div className="mt-0.5"><Change value={coin.priceChange.m5} compact /></div></div><div><div className="text-slate-700">LIQ</div><div className="mt-0.5 font-bold text-slate-300">{fmtMoney(coin.liquidityUsd)}</div></div><div><div className="text-slate-700">VOL 1H</div><div className="mt-0.5 font-bold text-slate-300">{fmtMoney(coin.volume.h1)}</div></div></div>
              </button>;
            })}
          </div>
        </div>
        <div className="min-w-0 space-y-4">
          {selectedCoin ? <>
            <div className="overflow-hidden rounded-3xl border border-white/10 bg-[#0b0e11]">
              <div className="flex flex-col gap-4 border-b border-white/[0.07] p-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex min-w-0 items-center gap-3"><CoinAvatar coin={selectedCoin} size="lg" /><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h1 className="text-xl font-black text-white">${selectedCoin.symbol}</h1><ScoreBadge coin={selectedCoin} /><span className="rounded-md border border-white/[0.07] bg-white/[0.03] px-2 py-1 text-[9px] font-black text-slate-500">{selectedCoin.dexId.toUpperCase()}</span></div><div className="mt-1 truncate text-xs text-slate-500">{selectedCoin.name} · {shortAddress(selectedCoin.address)} · {ageLabel(selectedCoin.ageMinutes)}</div></div></div>
                <div className="flex items-end gap-4 sm:text-right"><div><div className="text-2xl font-black text-white">{fmtPrice(selectedCoin.priceUsd)}</div><div className="mt-1 flex items-center gap-2 sm:justify-end"><Change value={selectedCoin.priceChange.m5} /><span className="text-[9px] text-slate-600">5m</span></div></div>{selectedCoin.dexUrl && <a href={selectedCoin.dexUrl} target="_blank" rel="noreferrer" className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/10 bg-white/[0.03] text-slate-400 hover:text-white"><ExternalLink className="h-4 w-4" /></a>}</div>
              </div>
              <div className="grid grid-cols-2 gap-px bg-white/[0.05] sm:grid-cols-4"><div className="bg-[#0b0e11] p-4"><div className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-700">Market Cap</div><div className="mt-1 text-base font-black text-white">{fmtMoney(selectedCoin.marketCap || selectedCoin.fdv)}</div></div><div className="bg-[#0b0e11] p-4"><div className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-700">Liquidity</div><div className="mt-1 text-base font-black text-white">{fmtMoney(selectedCoin.liquidityUsd)}</div></div><div className="bg-[#0b0e11] p-4"><div className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-700">Volume 1h</div><div className="mt-1 text-base font-black text-white">{fmtMoney(selectedCoin.volume.h1)}</div></div><div className="bg-[#0b0e11] p-4"><div className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-700">5m Trades</div><div className="mt-1 text-base font-black text-white">{selectedCoin.txns.m5.buys + selectedCoin.txns.m5.sells}<span className="ml-2 text-[9px] text-emerald-300">{selectedCoin.txns.m5.buys}B</span><span className="ml-1 text-[9px] text-red-300">{selectedCoin.txns.m5.sells}S</span></div></div></div>
            </div>

            <div className="overflow-hidden rounded-3xl border border-white/10 bg-[#0b0e11]">
              <div className="flex flex-col gap-3 border-b border-white/[0.07] p-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <div className={`flex items-center gap-2 text-[9px] font-black uppercase tracking-[0.18em] ${tapeOnline && selectedFlowSupported ? 'text-emerald-300' : 'text-amber-200'}`}><span className={`h-2 w-2 rounded-full ${tapeOnline && selectedFlowSupported ? 'animate-pulse bg-emerald-300' : 'bg-amber-300'}`} />{tapeOnline && selectedFlowSupported ? 'VERIFIED ON-CHAIN ORDER FLOW' : 'ORDER-FLOW COVERAGE'}</div>
                  <div className="mt-1 text-sm font-black text-white">${selectedCoin.symbol} · {selectedFlowSupported ? 'проверени PumpSwap транзакции' : 'за този DEX няма проверен поток'}</div>
                </div>
                <div className={`text-[9px] ${tapeOnline && selectedFlowSupported ? 'text-slate-500' : 'text-amber-200/80'}`}>{flowStatusLabel} · guard {state?.config.position_scan_seconds ?? 2}s</div>
              </div>
              <div className="grid grid-cols-2 gap-px bg-white/[0.05] sm:grid-cols-4">
                <div className="bg-[#0b0e11] p-3"><div className="text-[8px] font-black uppercase text-slate-700">BUY 60s</div><div className="mt-1 text-sm font-black text-emerald-300">{detail?.flow ? fmtMoney(detail.flow.buy_usd) : '—'}</div></div>
                <div className="bg-[#0b0e11] p-3"><div className="text-[8px] font-black uppercase text-slate-700">SELL 60s</div><div className="mt-1 text-sm font-black text-red-300">{detail?.flow ? fmtMoney(detail.flow.sell_usd) : '—'}</div></div>
                <div className="bg-[#0b0e11] p-3"><div className="text-[8px] font-black uppercase text-slate-700">BUY/SELL</div><div className="mt-1 text-sm font-black text-white">{detail?.flow ? `${detail.flow.buy_sell_usd_ratio.toFixed(2)}x` : '—'}</div></div>
                <div className="bg-[#0b0e11] p-3"><div className="text-[8px] font-black uppercase text-slate-700">WALLETS 60s</div><div className="mt-1 text-sm font-black text-white">{detail?.flow?.unique_wallets ?? '—'}</div></div>
              </div>
              <div className="max-h-[280px] overflow-y-auto">
                {(detail?.live_tape || []).length ? (detail?.live_tape || []).slice(0, 40).map(tx => <a key={tx.signature} href={`https://solscan.io/tx/${tx.signature}`} target="_blank" rel="noreferrer" className="grid grid-cols-[62px_48px_minmax(70px,1fr)_92px_86px] items-center gap-2 border-b border-white/[0.05] px-4 py-2.5 text-[9px] hover:bg-white/[0.025]">
                  <span className="font-mono text-slate-600">{tapeTimeLabel(tx.ts)}</span>
                  <span className={`font-black ${tx.direction === 'BUY' ? 'text-emerald-300' : 'text-red-300'}`}>{tx.direction}</span>
                  <span className="truncate font-black text-white">{fmtMoney(tx.usd_amount)}</span>
                  <span className="truncate font-mono text-slate-500">{shortAddress(tx.wallet)}</span>
                  <span className={`truncate text-right font-black ${tx.note.includes('WHALE') ? 'text-amber-300' : tx.direction === 'BUY' ? 'text-emerald-300/70' : 'text-red-300/70'}`}>{tx.note}</span>
                </a>) : <div className="p-8 text-center text-[10px] leading-5 text-slate-600">{detail ? 'Няма on-chain сделки в последния отговор за този pair.' : 'Данните за on-chain сделки още не са заредени.'}</div>}
              </div>
            </div>

            <div className="overflow-hidden rounded-3xl border border-white/10 bg-[#0b0e11]">
              <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-3"><div><div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">Live chart</div><div className="mt-0.5 text-sm font-black text-white">${selectedCoin.symbol} / SOL</div></div><Activity className="h-4 w-4 text-emerald-300" /></div>
              {dexEmbed ? <iframe title={`${selectedCoin.symbol} live chart`} src={dexEmbed} className="h-[430px] w-full border-0 bg-[#07090b]" loading="lazy" /> : <div className="flex h-[430px] items-center justify-center text-xs text-slate-600">Няма pair chart.</div>}
            </div>
            <div className="grid gap-4 lg:grid-cols-[1.1fr_.9fr]">
              <div className="rounded-3xl border border-white/10 bg-[#0b0e11] p-4">
                <div className="flex items-center justify-between"><div><div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">NEO price track</div><div className="mt-1 text-sm font-black text-white">Backend samples</div></div><Gauge className="h-4 w-4 text-emerald-300" /></div>
                <div className="mt-4 h-[190px]">
                  {chartData.length > 1 ? <ResponsiveContainer width="100%" height="100%"><AreaChart data={chartData}><defs><linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor="#34d399" stopOpacity={0.3}/><stop offset="95%" stopColor="#34d399" stopOpacity={0}/></linearGradient></defs><XAxis dataKey="label" hide /><YAxis domain={['dataMin','dataMax']} hide /><Tooltip contentStyle={{ background: '#0a0d10', border: '1px solid rgba(255,255,255,.1)', borderRadius: 12, fontSize: 11 }} formatter={(value: number | string) => [fmtPrice(Number(value)), 'Price']} labelFormatter={(label) => String(label)} /><Area type="monotone" dataKey="price" stroke="#34d399" fill="url(#priceFill)" strokeWidth={2} dot={false} /></AreaChart></ResponsiveContainer> : <div className="flex h-full items-center justify-center rounded-2xl border border-dashed border-white/[0.07] text-center text-[10px] leading-5 text-slate-600">NEO събира собствена price history на всеки scan.<br/>След няколко минути графиката се запълва.</div>}
                </div>
                <div className="mt-3 grid grid-cols-4 gap-2 border-t border-white/[0.06] pt-3"><div><div className="text-[8px] font-black text-slate-700">5M</div><Change value={selectedCoin.priceChange.m5} compact /></div><div><div className="text-[8px] font-black text-slate-700">1H</div><Change value={selectedCoin.priceChange.h1} compact /></div><div><div className="text-[8px] font-black text-slate-700">6H</div><Change value={selectedCoin.priceChange.h6} compact /></div><div><div className="text-[8px] font-black text-slate-700">24H</div><Change value={selectedCoin.priceChange.h24} compact /></div></div>
              </div>
              <div className="rounded-3xl border border-white/10 bg-[#0b0e11] p-4">
                <div className="flex items-center justify-between"><div><div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">NEO анализ</div><div className="mt-1 text-sm font-black text-white">Защо {selectedCoin.posture}</div></div><ShieldCheck className="h-4 w-4 text-emerald-300" /></div>
                <div className="mt-4 space-y-2">{selectedCoin.signals.length ? selectedCoin.signals.map((item, i) => <div key={`${item.title}-${i}`} className={`rounded-xl border p-3 ${item.kind === 'positive' ? 'border-emerald-400/15 bg-emerald-400/[0.05]' : item.kind === 'risk' ? 'border-red-400/15 bg-red-400/[0.05]' : 'border-white/[0.07] bg-white/[0.02]'}`}><div className={`text-[10px] font-black ${item.kind === 'positive' ? 'text-emerald-300' : item.kind === 'risk' ? 'text-red-300' : 'text-slate-300'}`}>{item.title}</div><div className="mt-1 text-[9px] leading-4 text-slate-600">{item.detail}</div></div>) : <div className="text-xs text-slate-600">Няма сигнали.</div>}</div>
              </div>
            </div>
          </> : <div className="flex min-h-[500px] items-center justify-center rounded-3xl border border-white/10 bg-[#0b0e11] text-xs text-slate-600">{state ? 'В получения отговор още няма market scan.' : emptyStateMessage}</div>}
        </div>
        <aside className="space-y-4">
          <div className="rounded-3xl border border-emerald-400/20 bg-[#09100d] p-4">
            <div className="flex items-start justify-between gap-3"><div><div className="text-[9px] font-black uppercase tracking-[0.18em] text-emerald-300">NEO AUTO BOT</div><h2 className="mt-1 text-lg font-black text-white">Paper engine</h2></div><div className={`rounded-lg border px-2 py-1 text-[9px] font-black ${connected && state?.running ? 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300' : 'border-white/10 bg-white/[0.03] text-slate-500'}`}>{connected ? state?.running ? 'RUNNING' : 'PAUSED' : connectionStatus}</div></div>
            <div className="mt-4 rounded-2xl border border-emerald-400/15 bg-emerald-400/[0.045] p-3">
              <div className="flex items-end justify-between gap-3"><div><div className="text-[8px] font-black uppercase tracking-[0.14em] text-emerald-300/70">Обща PAPER сметка</div><div className="mt-1 text-2xl font-black text-white">{moneyOrUnavailable(state?.stats.demo_balance_usd)}</div></div><div className="text-right"><div className={`text-sm font-black ${state && state.stats.return_pct >= 0 ? 'text-emerald-300' : state ? 'text-red-300' : 'text-slate-500'}`}>{percentageOrUnavailable(state?.stats.return_pct)}</div><div className="mt-1 text-[8px] text-slate-600">session return</div></div></div>
              <div className="mt-3 grid grid-cols-3 gap-2 border-t border-white/[0.06] pt-3 text-[9px]"><div><div className="text-slate-700">EQUITY</div><div className="mt-0.5 font-black text-white">{moneyOrUnavailable(state?.stats.demo_equity_usd)}</div></div><div><div className="text-slate-700">AVAILABLE</div><div className="mt-0.5 font-black text-white">{moneyOrUnavailable(state?.stats.demo_available_usd)}</div></div><div><div className="text-slate-700">RESERVED</div><div className="mt-0.5 font-black text-white">{moneyOrUnavailable(state?.stats.demo_reserved_usd)}</div></div></div>
              <div className="mt-2 text-[8px] text-slate-600">Session {state?.stats.demo_session_id || '—'} · от {fullTimeLabel(state?.stats.demo_started_at || 0)}</div>
            </div>
            {!!state?.config.ensemble_strategies?.length && <div data-testid="paper-ensemble" className="mt-2 rounded-xl border border-cyan-300/15 bg-cyan-400/[0.04] p-3 text-[9px] leading-4 text-cyan-100">
              <div className="font-black">Една PAPER сметка · {state.config.ensemble_strategies.length} паралелни стратегии</div>
              <div className="mt-1 text-slate-500">Съвпадналите сигнали водят до една позиция за токен и използват общия наличен капитал. Отворени: {state.stats.open_positions}/{state.config.max_positions}.</div>
              <div className="mt-2 grid grid-cols-2 gap-1">{state.config.ensemble_strategies.map(id => <button key={id} type="button" aria-expanded={selectedPaperStrategy === id} onClick={() => setSelectedPaperStrategy(current => current === id ? '' : id)} className={`rounded-lg border px-2 py-1.5 text-left font-bold ${selectedPaperStrategy === id ? 'border-cyan-300/35 bg-cyan-300/10 text-white' : 'border-white/10 text-cyan-100/75 hover:text-white'}`}>{strategyName[id] || id}</button>)}</div>
              <div className="mt-2 text-slate-500">Пазарен сигнал; входът и изходът са моделирани PAPER изпълнения.</div>
            </div>}
            {selectedPaperStrategy && state?.config.ensemble_strategies?.includes(selectedPaperStrategy) && <div className="mt-2 rounded-xl border border-cyan-300/15 bg-black/20 p-3 text-[9px] leading-4">
              <div className="font-black text-cyan-100">{strategyName[selectedPaperStrategy] || selectedPaperStrategy} · сделки в общата сметка</div>
              <div className="mt-1 text-slate-500">Сделка с повече от един сигнал се вижда при всяко съвпаднало правило, но PnL се отчита само веднъж в общия баланс.</div>
              {selectedPaperTrades.length ? <div className="mt-2 max-h-64 space-y-1 overflow-y-auto">{selectedPaperTrades.map(({ trade, closed }) => <div key={`${closed ? 'closed' : 'open'}-${trade.id}`} className="rounded-lg border border-white/[0.06] p-2">
                <div className="flex items-center justify-between gap-2"><button type="button" onClick={() => setSelectedAddress(trade.address)} className="font-black text-white hover:text-cyan-100">#{trade.trade_no ?? '—'} · ${trade.symbol}</button><span className={trade.pnl_usd >= 0 ? 'text-emerald-300' : 'text-red-300'}>{trade.pnl_usd >= 0 ? '+' : ''}${trade.pnl_usd.toFixed(2)}</span></div>
                <div className="mt-0.5 text-slate-500">{closed ? 'Затворена' : 'Отворена'} · {fullTimeLabel(trade.opened_at)} · размер ${trade.notional_usd.toFixed(2)}</div>
                {(trade.dex_url || trade.pairAddress) && <a href={trade.dex_url || `https://dexscreener.com/solana/${encodeURIComponent(trade.pairAddress)}`} target="_blank" rel="noopener noreferrer" className="mt-1 inline-flex items-center gap-1 text-cyan-200 hover:text-white">DEX Screener <ExternalLink className="h-3 w-3" /></a>}
              </div>)}</div> : <div className="mt-2 rounded-lg border border-dashed border-white/10 p-2 text-slate-500">Още няма изпълнени PAPER сделки по този сигнал.</div>}
            </div>}
            <div className="mt-2 grid grid-cols-2 gap-2"><div className="rounded-xl border border-white/[0.07] bg-black/20 p-3"><div className="text-[8px] font-black uppercase tracking-[0.12em] text-slate-700">Entry score</div><div className="mt-1 text-lg font-black text-white">{state ? `${state.config.entry_score}+` : '—'}</div></div><div className="rounded-xl border border-white/[0.07] bg-black/20 p-3"><div className="text-[8px] font-black uppercase tracking-[0.12em] text-slate-700">Trade size</div><div className="mt-1 text-lg font-black text-white">{moneyOrUnavailable(state?.config.trade_notional_usd, false, 0)}</div></div></div>
            <div className="mt-2 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 text-[9px] leading-4 text-slate-500">{state ? <>Планиран стоп {state.config.stop_loss_pct}% · TP {state.config.take_profit_pct}% · trailing {state.config.trailing_pct}% · max hold {state.config.max_hold_minutes}m · дневен лимит {state.config.max_daily_loss_usd === 0 ? 'ИЗКЛЮЧЕН' : `-$${state.config.max_daily_loss_usd}`}. Gap или липсващ sell route могат да увеличат загубата отвъд стопа.</> : 'Настройките на PAPER сметката още не са заредени.'}</div>
            <div className="mt-3 flex min-h-10 w-full items-center justify-center gap-2 rounded-xl border border-emerald-400/15 bg-emerald-400/[0.05] px-3 text-center text-[10px] font-black text-emerald-200"><ShieldCheck className="h-4 w-4" /> {connected && state?.running ? 'PAPER ПРАВИЛАТА СЛЕДЯТ ЗА ВАЛИДНИ ВХОДОВЕ' : 'АКТИВНОСТТА НА PAPER ENGINE НЕ Е ПОТВЪРДЕНА'}</div>
            <div className="mt-3 text-[9px] leading-4 text-slate-600">{connected ? state?.message : `${connectionLabel} · ${snapshotHint}`}</div>
          </div>

          <div className="rounded-3xl border border-white/10 bg-[#0b0e11] p-4">
            <div className="flex items-center justify-between"><div><div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">Open positions</div><h3 className="mt-1 text-base font-black text-white">Автоматични входове</h3></div><WalletCards className="h-4 w-4 text-emerald-300" /></div>
            <div className="mt-4 space-y-2">
              {(state?.positions || []).length === 0 && <div className="rounded-xl border border-dashed border-white/[0.08] p-4 text-center text-[10px] leading-5 text-slate-600">{state ? 'В последния отговор няма отворени позиции. Нов вход изисква валиден сигнал, котировка и свободен капитал.' : 'Данните за позициите още не са заредени.'}</div>}
              {state?.positions.map(position => <div key={position.id} className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-3">
                <button onClick={() => setSelectedAddress(position.address)} className="w-full text-left hover:opacity-80"><div className="flex items-center justify-between gap-2"><div><div className="text-xs font-black text-white">${position.symbol}</div><div className="mt-0.5 text-[9px] text-slate-600">#{position.trade_no ?? '—'} · вход {fmtPrice(position.execution_entry_price ?? position.entry_price)} · ${position.notional_usd.toFixed(0)}</div></div><div className={`text-sm font-black ${position.pnl_pct >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{position.pnl_pct >= 0 ? '+' : ''}{position.pnl_pct.toFixed(2)}%</div></div></button>
                {!!position.strategy_matches?.length && <div className="mt-2 text-[9px] text-cyan-200/80">Сигнали: {strategyLabels(position.strategy_matches)}</div>}
                <div className="mt-2 flex items-center justify-between text-[9px] text-slate-600"><span>{position.pnl_usd >= 0 ? '+' : ''}${position.pnl_usd.toFixed(2)} · Score {position.current_score?.toFixed(0) ?? position.score.toFixed(0)}</span><span>{Math.max(0, Math.round((Date.now() - position.opened_at) / 60000))}m open</span></div>
                {(position.dex_url || position.pairAddress) && <a href={position.dex_url || `https://dexscreener.com/solana/${encodeURIComponent(position.pairAddress)}`} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-1 rounded-lg border border-cyan-300/20 px-2 py-1 text-[9px] font-black text-cyan-200">DEX Screener <ExternalLink className="h-3 w-3" /></a>}
              </div>)}
            </div>
          </div>
          <div className="rounded-3xl border border-white/10 bg-[#0b0e11] p-4">
            <div className="flex items-center justify-between"><div><div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">Activity</div><h3 className="mt-1 text-base font-black text-white">Какво прави NEO</h3></div><Bot className="h-4 w-4 text-emerald-300" /></div>
            <div className="mt-4 space-y-3">{(state?.events || []).slice(0, 8).map(event => <div key={`${event.ts}-${event.text}`} className="flex gap-2.5"><div className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-300" /><div><div className="text-[10px] leading-4 text-slate-400">{event.text}</div><div className="mt-0.5 text-[8px] text-slate-700">{timeLabel(event.ts)}</div></div></div>)}</div>
            <div className="mt-4 border-t border-white/[0.06] pt-3"><button onClick={() => { if (window.confirm('Да започна ли НОВА ЛИЧНА demo сесия с $1,000? Това засяга само твоя dashboard и не пипа GOLD engine-а.')) resetMyDemo(); }} className="h-8 w-full rounded-lg border border-white/[0.07] bg-transparent text-[9px] font-black text-slate-600 hover:bg-white/[0.03] hover:text-white">RESET МОЯТА DEMO → $1,000</button></div>
          </div>
        </aside>
      </section>

      <section className="mt-4 overflow-hidden rounded-3xl border border-cyan-400/15 bg-[#0b0e11]">
        <div className="flex flex-col gap-3 border-b border-white/[0.07] p-4 sm:flex-row sm:items-center sm:justify-between">
          <div><div className="text-[9px] font-black uppercase tracking-[0.18em] text-cyan-300">MULTI-STRATEGY LAB</div><h2 className="mt-1 text-lg font-black text-white">Отделни обучителни PAPER портфейли · извън горната сметка</h2><div className="mt-1 text-[9px] text-slate-600">Четирите избрани стратегии имат по $250 тестов капитал и до 25% на позиция. Балансите и резултатите им не се сумират с общата PAPER сметка.</div></div>
          <div className={`rounded-lg border px-2.5 py-1.5 text-[9px] font-black ${connected && state?.strategy_lab?.status === 'online' ? 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300' : 'border-amber-400/20 bg-amber-400/10 text-amber-200'}`}>{connected ? (state?.strategy_lab?.status || 'UNKNOWN').toUpperCase() : connectionLabel}</div>
        </div>
        <div data-testid="lab-integrity-warning" className="mx-4 mt-3 rounded-xl border border-amber-400/20 bg-amber-400/[0.04] px-4 py-3 text-xs leading-5 text-amber-100">В историята на Lab има несъответстващи цени, включително XFUN. Сумите не са пренаписани. Новите входове изискват проверка на точния pool от втори източник.</div>
        {state?.strategy_lab?.portfolio_setup?.version && state.strategy_lab.portfolio_setup.status !== 'UNCONFIGURED' && <div data-testid="lab-promoted-paper-cohort" className="mx-4 mt-3 rounded-xl border border-emerald-400/20 bg-emerald-400/[0.035] px-4 py-3 text-[10px] leading-5 text-emerald-50">
          <div className="font-black">{state.strategy_lab.portfolio_setup.status === 'DRAINING' ? 'Подготовка на избрания обучителен набор · текущите позиции се управляват до затваряне' : `Обучителен набор · $${state.strategy_lab.portfolio_setup.total_allocated_capital_usd?.toFixed(0) ?? '1,000'} разпределени в независими сметки`}</div>
          <div className="text-emerald-100/70">{state.strategy_lab.portfolio_setup.status === 'DRAINING' ? 'Преходът е в ход: няма нови входове; текущите позиции се прехвърлят в отделни exit-only PAPER книги.' : 'Началните резултати са архивирани; новото сравнение започва от нула.'} Исторически: {state.strategy_lab.portfolio_setup.historical_simulations ?? 0} симулации върху {state.strategy_lab.portfolio_setup.unique_market_episodes_30m ?? 0} приблизителни токен епизода (30 мин.). Епизодите се припокриват между стратегиите, а данните имат известни несъответствия.</div>
          {state.strategy_lab.portfolio_setup.promotion_error && <div className="mt-1 text-amber-200">Промоцията още не е завършена: {state.strategy_lab.portfolio_setup.promotion_error}</div>}
          <div className="mt-2 grid gap-1 sm:grid-cols-2 lg:grid-cols-4">{(state.strategy_lab.portfolio_setup.strategies || []).map(id => {
            const book = state.strategy_lab.books[id];
            const stats = state.strategy_lab.stats[id];
            if (!book) return null;
            return <div key={id} className="rounded-lg border border-emerald-300/10 bg-black/20 px-2.5 py-2"><b>{book.name}</b><div className="text-emerald-100/65">капитал ${book.allocation_usd ?? book.starting_balance} · equity ${(stats?.equity ?? book.balance).toFixed(2)} · {stats?.trades ?? 0} сделки</div><div className="text-emerald-100/65">{book.position ? `позиция ${book.position.symbol} · ${book.position.pnl_pct >= 0 ? '+' : ''}${book.position.pnl_pct.toFixed(2)}%` : 'няма отворена позиция'}</div></div>;
          })}</div>
          {!!Object.keys(state.strategy_lab.portfolio_setup.legacy_draining_books || {}).length && <div className="mt-2 rounded-lg border border-amber-300/15 bg-amber-300/[0.025] px-3 py-2 text-amber-100/80"><b>Стари PAPER изходи · отделно от новия капитал:</b> тези позиции се следят до затваряне, без нови входове, и не се добавят към $1,000 набора.{Object.values(state.strategy_lab.portfolio_setup.legacy_draining_books || {}).map(book => {
            const trade = book.history[0];
            return <div key={book.id} className="mt-1">{book.name}: {book.position ? `отворена ${book.position.symbol} · ${book.position.pnl_pct >= 0 ? '+' : ''}${book.position.pnl_pct.toFixed(2)}% · $${book.position.notional_usd.toFixed(2)}` : trade ? `затворена ${trade.symbol} · ${trade.pnl_usd != null && trade.pnl_usd >= 0 ? '+' : ''}$${trade.pnl_usd?.toFixed(2) ?? '—'} · ${trade.exit_reason || 'PAPER изход'}` : 'позицията вече е затворена'}</div>;
          })}</div>}
        </div>}
        <div className="mx-4 mt-3 rounded-xl border border-sky-400/15 bg-sky-400/[0.035] px-4 py-3 text-[10px] leading-5 text-sky-100"><b>Граница на PAPER симулацията:</b> Strategy Lab маркира по exact-pool spot цената от DEX Screener и моделира DEX такса, impact, slippage, забавяне и мрежов разход. Това не е Jupiter изпълнима котировка или Solana транзакция; резултатът не доказва какво би получил реален портфейл. Astra използва Jupiter котировка, но също не изпраща swap. Отворена позиция със стара котировка се отбелязва изрично и не се затваря по остаряла цена.</div>
        {state?.strategy_lab?.astra && <AstraBrainPanel data={state.strategy_lab.astra} />}
        <LabPairedPanel data={state?.strategy_lab?.paired} />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1100px] text-left">
            <thead><tr className="border-b border-white/[0.06] text-[8px] font-black uppercase tracking-[0.14em] text-slate-700"><th className="px-4 py-3">Стратегия</th><th className="px-4 py-3">Balance</th><th className="px-4 py-3">Equity</th><th className="px-4 py-3">PnL</th><th className="px-4 py-3">Сделки</th><th className="px-4 py-3">Win rate</th><th className="px-4 py-3">PF</th><th className="px-4 py-3">Отворена позиция</th></tr></thead>
            <tbody>
              {Object.values(state?.strategy_lab?.books || {}).filter(book => book.id !== 'ASTRA_6_BRAIN').sort((a,b) => Number(b.portfolio_group === 'PROMOTED_PAPER' || b.portfolio_group === 'PROMOTION_DRAINING') - Number(a.portfolio_group === 'PROMOTED_PAPER' || a.portfolio_group === 'PROMOTION_DRAINING') || (state?.strategy_lab?.stats?.[b.id]?.equity ?? b.balance) - (state?.strategy_lab?.stats?.[a.id]?.equity ?? a.balance)).map(book => {
                const st = state?.strategy_lab?.stats?.[book.id];
                const pnl = st?.realized_pnl ?? (book.balance - book.starting_balance);
                const expanded = selectedLabStrategyId === book.id;
                const trades = book.history || [];
                const referencePair = book.position?.pairAddress || trades[0]?.pairAddress;
                const dexUrl = referencePair ? `https://dexscreener.com/solana/${encodeURIComponent(referencePair)}` : '';
                const bookDexButton = dexUrl
                  ? <a href={dexUrl} target="_blank" rel="noopener noreferrer" onClick={event => event.stopPropagation()} aria-label={`Отвори ${book.name} в DexScreener`} className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1.5 text-[8px] font-black text-slate-400 hover:border-cyan-300/25 hover:text-cyan-200">DEX <ExternalLink className="h-3 w-3" /></a>
                  : <span title="Ще има адрес след първа изпълнена PAPER позиция" className="inline-flex items-center gap-1 rounded-lg border border-white/[0.05] px-2 py-1.5 text-[8px] font-black text-slate-700">DEX · няма pool</span>;
                return <Fragment key={book.id}>
                <tr className={`border-b border-white/[0.04] text-xs hover:bg-white/[0.02] ${expanded ? 'bg-cyan-400/[0.025]' : ''}`}>
                  <td className="px-4 py-3"><button type="button" aria-expanded={expanded} onClick={() => setSelectedLabStrategyId(expanded ? '' : book.id)} className="text-left"><div className="font-black text-white">{book.name}<span className={`ml-2 rounded border px-1.5 py-0.5 text-[8px] font-black ${book.portfolio_group === 'PROMOTED_PAPER' ? 'border-emerald-300/20 text-emerald-200' : book.portfolio_group === 'PROMOTION_DRAINING' ? 'border-amber-300/20 text-amber-200' : 'border-white/[0.06] text-slate-600'}`}>{book.portfolio_group === 'PROMOTED_PAPER' ? `PAPER $${book.allocation_usd ?? book.starting_balance}` : book.portfolio_group === 'PROMOTION_DRAINING' ? 'ПОДГОТОВКА' : 'ТЕСТ'}</span><span className="ml-2 text-[9px] font-medium text-cyan-200/70">{expanded ? 'сгъни' : 'сделки'} · {st?.trades ?? trades.length}</span></div><div className="mt-1 text-[8px] font-mono text-slate-700">{book.id}</div></button>{book.id === 'FLOW_MOMENTUM_SCALE_OUT' && <div className="mt-1 text-[9px] text-amber-200/80">Общ изход −3/+10; близките входове могат да дадат еднакви сделки.</div>}</td>
                  <td className="px-4 py-3 font-black text-white">${book.balance.toFixed(2)}</td>
                  <td className={`px-4 py-3 ${st?.valuation_stale ? 'text-amber-200' : 'text-slate-300'}`}>{st?.valuation_stale ? '~' : ''}${(st?.equity ?? book.balance).toFixed(2)}{st?.valuation_stale && <div className="mt-1 text-[8px]">стара оценка</div>}</td>
                  <td className={`px-4 py-3 font-black ${pnl >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}<div className="mt-1 text-[9px]">{(st?.return_pct ?? 0) >= 0 ? '+' : ''}{(st?.return_pct ?? 0).toFixed(2)}%</div></td>
                  <td className="px-4 py-3 text-slate-400">{st?.trades ?? 0}<div className="mt-1 text-[9px] text-slate-700">{st?.wins ?? 0}W / {st?.losses ?? 0}L</div></td>
                  <td className="px-4 py-3 font-black text-white">{st?.trades ? `${st.win_rate.toFixed(1)}%` : '—'}</td>
                  <td className="px-4 py-3 text-slate-300">{st?.profit_factor != null ? st.profit_factor.toFixed(2) : st?.wins && !st.losses ? '∞ (няма загуби)' : '—'}</td>
                  <td className="px-4 py-3"><div className="flex items-center gap-2">{book.position ? <button onClick={() => setSelectedAddress(book.position!.address)} className="rounded-xl border border-cyan-400/15 bg-cyan-400/[0.05] px-3 py-2 text-left"><div className="font-black text-cyan-200">${book.position.symbol}</div><div className={`mt-1 text-[9px] font-black ${book.position.quote_status === 'fresh' ? ((book.position.pnl_pct || 0) >= 0 ? 'text-emerald-300' : 'text-red-300') : 'text-amber-200'}`}>{book.position.quote_status === 'fresh' ? `${(book.position.pnl_pct || 0) >= 0 ? '+' : ''}${(book.position.pnl_pct || 0).toFixed(2)}%` : 'КОТИРОВКА СТАРА'} · ${book.position.notional_usd.toFixed(0)}</div></button> : <span className="text-[9px] text-slate-700">чака setup</span>}{bookDexButton}</div></td>
                </tr>
                {expanded && <tr className="border-b border-cyan-400/10 bg-black/20"><td colSpan={8} className="px-4 py-4">
                  {book.position && <div className="mb-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-cyan-300/20 bg-cyan-400/[0.045] p-3"><div><div className="text-[9px] font-black uppercase tracking-wide text-cyan-200">Отворена PAPER позиция · {book.position.execution_mode || 'моделирано изпълнение'}</div><div className="mt-1 text-xs font-black text-white">${book.position.symbol} · вход {fmtPrice(book.position.execution_entry_price ?? book.position.entry_price ?? 0)} · текуща оценка {fmtPrice(book.position.current_price ?? 0)} · размер ${book.position.notional_usd.toFixed(2)}</div><div className="mt-1 text-[9px] text-slate-400">Нереализиран PnL {book.position.open_pnl_usd != null ? `${book.position.open_pnl_usd >= 0 ? '+' : ''}$${book.position.open_pnl_usd.toFixed(2)}` : '—'} · вход DEX такса ${book.position.entry_dex_fee_usd?.toFixed(3) ?? '—'} · мрежа ${book.position.entry_network_fee_usd?.toFixed(3) ?? '—'} · оценка изходна такса ${book.position.estimated_exit_fee_usd?.toFixed(3) ?? '—'}</div></div><div className="flex items-center gap-2"><button onClick={() => setSelectedAddress(book.position!.address)} className="rounded-lg border border-white/10 px-2.5 py-2 text-[9px] font-black text-slate-300 hover:text-white">Токен</button>{book.position.pairAddress && <a href={`https://dexscreener.com/solana/${encodeURIComponent(book.position.pairAddress)}`} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 rounded-lg border border-cyan-300/20 px-2.5 py-2 text-[9px] font-black text-cyan-100">DEX <ExternalLink className="h-3 w-3" /></a>}</div></div>}
                  {book.position && book.position.quote_status !== 'fresh' && <div className="mb-3 rounded-lg border border-amber-300/20 bg-amber-300/[0.04] px-3 py-2 text-[10px] leading-5 text-amber-100">Няма достатъчно прясна котировка за точния pool. Показаният PnL е последната оценка от {Math.max(0, Math.floor((book.position.quote_age_ms ?? (Date.now() - (book.position.updated_at ?? 0))) / 1000))} сек. и не е текущ; позицията не се затваря по тази цена.</div>}
                  {trades.length ? <div className="space-y-2">{trades.map((trade, index) => <article key={`${book.id}-${trade.trade_no ?? trade.opened_at}-${index}`} className="grid gap-3 rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 text-[10px] md:grid-cols-[1fr_auto] md:items-center"><div><div className="flex flex-wrap items-center gap-x-3 gap-y-1"><b className="text-xs text-white">#{trade.trade_no ?? '—'} · ${trade.symbol}</b><span className="text-slate-500">Score {trade.score?.toFixed(0) ?? '—'} · {trade.execution_mode || 'моделиран PAPER'}</span><span className="text-slate-500">{fullTimeLabel(trade.opened_at)} → {fullTimeLabel(trade.closed_at || 0)}</span></div><div className="mt-1 text-slate-400">Вход {fmtPrice(trade.execution_entry_price ?? trade.entry_price ?? 0)} → изход {fmtPrice(trade.execution_exit_price ?? trade.exit_price ?? 0)} · размер ${trade.notional_usd.toFixed(2)} · {trade.exit_reason || 'изходът още не е записан'}</div><div className="mt-1 text-slate-500">DEX такси ${(trade.entry_dex_fee_usd ?? 0).toFixed(3)} + ${(trade.exit_dex_fee_usd ?? 0).toFixed(3)} · мрежа ${(trade.entry_network_fee_usd ?? 0).toFixed(3)} + ${(trade.exit_network_fee_usd ?? 0).toFixed(3)} · impact {trade.entry_price_impact_pct?.toFixed(2) ?? '—'}% / {trade.exit_price_impact_pct?.toFixed(2) ?? '—'}% · slippage {trade.entry_slippage_pct?.toFixed(2) ?? '—'}% / {trade.exit_slippage_pct?.toFixed(2) ?? '—'}%</div></div><div className="flex items-center justify-between gap-3 md:justify-end"><b className={(trade.pnl_usd ?? 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}>{(trade.pnl_usd ?? 0) >= 0 ? '+' : ''}${(trade.pnl_usd ?? 0).toFixed(2)} · {(trade.pnl_pct ?? 0) >= 0 ? '+' : ''}{(trade.pnl_pct ?? 0).toFixed(2)}%</b>{trade.pairAddress && <a href={`https://dexscreener.com/solana/${encodeURIComponent(trade.pairAddress)}`} target="_blank" rel="noopener noreferrer" aria-label={`Свери ${trade.symbol} в DexScreener`} className="inline-flex items-center gap-1 rounded-lg border border-cyan-300/20 px-2.5 py-2 font-black text-cyan-100">DEX <ExternalLink className="h-3 w-3" /></a>}</div></article>)}</div> : !book.position ? <div className="rounded-lg border border-dashed border-white/[0.07] p-4 text-[10px] text-slate-500">Тази стратегия още няма приключила или отворена PAPER сделка. DEX pool ще се покаже при първа валидна позиция; не се измисля адрес за графика.</div> : <div className="text-[10px] text-slate-500">Няма приключили PAPER сделки за тази стратегия.</div>}
                </td></tr>}
                </Fragment>
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section data-testid="execution-integrity" className="mt-4 rounded-2xl border border-sky-400/20 bg-sky-400/[0.04] p-4 text-xs leading-6 text-slate-300">
        <div className="font-bold text-white">PAPER сигналите и моделираното изпълнение се проверяват отделно</div>
        <p>{state ? <>Сигнал: {state.config.signal_strategy ?? 'Не е посочен'} · Стоп −{state.config.stop_loss_pct}% нето · Цел +{state.config.take_profit_pct}% нето.</> : 'Настройките на изпълнението още не са заредени от backend.'}</p>
        <p>Цените по-долу са от симулираното изпълнение, когато са налични, а не от графиката. Котировката не е изпълнена транзакция. Мрежовите разходи и допълнителният буфер остават оценки.</p>
        <p className="mt-1 text-amber-200">При reset старата PAPER история се архивира и започва нова сесия. Архивните и симулираните резултати не доказват бъдеща доходност.</p>
      </section>

      <section className="mt-4 overflow-hidden rounded-3xl border border-white/10 bg-[#0b0e11]">
        <div className="flex flex-col gap-3 border-b border-white/[0.07] p-4 sm:flex-row sm:items-center sm:justify-between"><div><div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">Paper history</div><h2 className="mt-1 text-lg font-black text-white">История на симулираните сделки</h2></div><div className="flex items-center gap-2 text-[9px] text-slate-600"><CircleDollarSign className="h-4 w-4 text-emerald-300" /> {state ? state.stats.closed_trades : '—'} затворени · balance {moneyOrUnavailable(state?.stats.demo_balance_usd)}</div></div>
        <div className="overflow-x-auto"><table className="w-full min-w-[1450px] text-left"><thead><tr className="border-b border-white/[0.06] text-[8px] font-black uppercase tracking-[0.14em] text-slate-700"><th className="px-4 py-3"># / Coin</th><th className="px-4 py-3">Вход време</th><th className="px-4 py-3">Симулиран вход</th><th className="px-4 py-3">Изход време</th><th className="px-4 py-3">Симулиран изход</th><th className="px-4 py-3">Размер</th><th className="px-4 py-3">PnL</th><th className="px-4 py-3">Balance</th><th className="px-4 py-3">Score</th><th className="px-4 py-3">Изход</th><th className="px-4 py-3">Hold</th><th className="px-4 py-3">Проверка</th></tr></thead><tbody>{(state?.history || []).length === 0 ? <tr><td colSpan={12} className="px-4 py-8 text-center text-xs text-slate-600">{state ? 'В последния отговор няма затворени PAPER сделки.' : 'Историята още не е заредена от backend.'}</td></tr> : state?.history.slice(0, 100).map(trade => <tr key={trade.id} onClick={() => setSelectedAddress(trade.address)} className="cursor-pointer border-b border-white/[0.04] text-xs hover:bg-white/[0.02]"><td className="px-4 py-3"><div className="font-black text-white">#{trade.trade_no ?? '—'} · ${trade.symbol}</div><div className="mt-1 text-[9px] text-slate-700">{shortAddress(trade.address)}</div>{!!trade.strategy_matches?.length && <div className="mt-1 max-w-48 text-[9px] leading-4 text-cyan-200/80">{strategyLabels(trade.strategy_matches)}</div>}</td><td className="px-4 py-3 text-[10px] text-slate-500">{fullTimeLabel(trade.opened_at)}</td><td className="px-4 py-3 text-slate-300">{fmtPrice(trade.execution_entry_price ?? trade.entry_price)}</td><td className="px-4 py-3 text-[10px] text-slate-500">{fullTimeLabel(trade.closed_at || trade.updated_at)}</td><td className="px-4 py-3 text-slate-300">{fmtPrice(trade.execution_exit_price ?? trade.exit_price ?? trade.current_price)}</td><td className="px-4 py-3 text-slate-400">${trade.notional_usd.toFixed(2)}</td><td className={`px-4 py-3 font-black ${(trade.pnl_pct || 0) >= 0 ? 'text-emerald-300' : 'text-red-300'}`}><div>{(trade.pnl_usd || 0) >= 0 ? '+' : ''}${(trade.pnl_usd || 0).toFixed(2)}</div><div className="mt-1 text-[9px]">{(trade.pnl_pct || 0) >= 0 ? '+' : ''}{(trade.pnl_pct || 0).toFixed(2)}%</div></td><td className="px-4 py-3"><div className="text-slate-500">${(trade.balance_before ?? 0).toFixed(2)}</div><div className="mt-1 font-black text-white">→ ${(trade.balance_after ?? 0).toFixed(2)}</div></td><td className="px-4 py-3 text-slate-400">{trade.score?.toFixed(0)}</td><td className="px-4 py-3 text-slate-400">{trade.exit_reason || '—'}</td><td className="px-4 py-3 text-slate-500">{durationLabel(trade.opened_at, trade.closed_at)}</td><td className="px-4 py-3">{trade.dex_url ? <a onClick={e => e.stopPropagation()} href={trade.dex_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1.5 text-[9px] font-black text-slate-400 hover:text-white">CHART <ExternalLink className="h-3 w-3" /></a> : '—'}</td></tr>)}</tbody></table></div>
      </section>

      {state?.paper_training ? <PaperTrainingPanel data={state.paper_training} /> : <section className="mt-4 rounded-2xl border border-amber-400/20 bg-amber-400/[0.04] p-4 text-xs text-amber-100">{state ? 'Backend не е предоставил данни за обучителния PAPER режим. Активната версия и резултатите още не са потвърдени.' : 'Данните за обучителния PAPER режим още не са заредени от backend.'}</section>}
      <footer className="mt-5 flex flex-col justify-between gap-2 border-t border-white/[0.06] py-5 text-[9px] leading-4 text-slate-700 sm:flex-row"><div>NEO Meme Coins · live Solana market monitoring · isolated account engine</div><div className="max-w-2xl sm:text-right">Paper режимът е симулация. Meme coins са високорискови; score-ът е филтър за наблюдение, не обещание за печалба.</div></footer>
    </main>
  </div>;
}

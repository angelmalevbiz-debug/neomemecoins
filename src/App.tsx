import LabPairedPanel, { type LabPairedSnapshot } from './components/LabPairedPanel';
import PaperTrainingPanel, { type PaperTrainingSnapshot } from './components/PaperTrainingPanel';
import AstraBrainPanel, { type AstraSnapshot } from './components/AstraBrainPanel';
import PaperPortfolioHistory from './components/PaperPortfolioHistory';
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity, ExternalLink, Flame, Gauge, RefreshCw, Search, ShieldCheck, Sparkles,
  TrendingDown, TrendingUp, WalletCards, Zap,
} from 'lucide-react';
import {
  Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { supabase } from './lib/supabase';
import { promotedPaperPortfolio } from './lib/paperPortfolioState';
import { isArchivedStrategy, partitionLabStrategies, planningCostStatus, quoteFailureStatus, type StrategyLifecycle, type CostFeasibility } from './lib/labStrategyView';
import LabEntryStatus from './components/LabEntryStatus';
import {
  backendErrorMessage, dashboardConnectionStatus, initialDashboardConnection,
  moneyOrUnavailable, paperApiConfiguration, percentageOrUnavailable, readAccountStateCache, tokenDetailForAddress, writeAccountStateCache,
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
type LabEntryDiagnostics = { signal_candidates?: number; matched_candidates?: number; cost_rejected?: number; cooldown_rejected?: number; affordable_candidates?: number; price_verification_rejected?: number; price_crosscheck_pending?: number; flow_missing_candidates?: number; flow_tape_status?: string; flow_tape_coverage_pct?: number; flow_tape_backlog?: number; blocked_reason?: string; brain_rejected_candidates?: number; temporal_warmup_candidates?: number; risk_rejected_candidates?: number; promoted_policy_version?: string; promoted_flow_rejected?: number; promoted_safety_rejected?: number; promoted_price_rejected?: number; promoted_cost_rejected?: number; promoted_max_entry_roundtrip_cost_pct?: number; promoted_cost_feasibility?: CostFeasibility; profitability_proven?: boolean };
type LabBook = { id: string; name: string; starting_balance: number; balance: number; portfolio_group?: 'PROMOTED_PAPER' | 'PROMOTION_DRAINING' | 'TEST'; promotion_pending?: boolean; runtime_compatibility?: { status?: string }; strategy_lifecycle?: StrategyLifecycle; allocation_usd?: number; max_position_fraction?: number; position: LabPosition | null; history: LabTrade[]; entry_diagnostics?: LabEntryDiagnostics };
type LabStats = { trades: number; wins: number; losses: number; win_rate: number; profit_factor: number | null; realized_pnl: number; unrealized_pnl?: number; total_pnl?: number; equity: number; return_pct: number; open: boolean; valuation_stale?: boolean; mark_age_ms?: number };
type StrategyLab = { paired?: LabPairedSnapshot; astra?: AstraSnapshot; execution_basis?: string; execution_note?: string; portfolio_setup?: { version?: string; status?: string; total_allocated_capital_usd?: number; allocation_per_strategy_usd?: number; max_position_fraction?: number; strategies?: string[]; historical_simulations?: number; unique_market_episodes_30m?: number; legacy_draining_books?: Record<string, LegacyLabBook>; legacy_open_position_count?: number; legacy_open_positions?: string[]; evidence_note?: string; promotion_error?: string }; status: string; updated_at: number; started_at: number; books: Record<string, LabBook>; stats: Record<string, LabStats>; error?: string };
type LiveTrade = { ts: number; direction: 'BUY' | 'SELL'; token_amount: number; usd_amount: number; wallet: string; note: string; address: string; pairAddress: string; symbol: string; signature: string; slot: number };
type FlowStats = { seconds: number; trades: number; buys: number; sells: number; buy_usd: number; sell_usd: number; buy_sell_usd_ratio: number; unique_wallets: number; max_buy_usd: number; max_sell_usd: number };
type TapeStatus = { status?: string; tracked_pairs?: number; updated_at?: number; source?: string; error?: string | null };
type EntryDiagnostics = { status?: string; message?: string; evaluated?: number; signal_passed?: number; quoted?: number; opened?: number; rejections?: Record<string, number>; reason_labels?: Record<string, string>; market_cost_feasibility?: CostFeasibility; quote_preparation_failures?: { symbol?: string; stage?: string; code?: string }[] };
type RuleLearning = { closed_trades: number; wins: number; win_rate_pct: number; net_pnl_usd: number; loss_reasons: Record<string, number>; throttled: boolean };
type StrategyLearning = { policy_version: string; closed_trades: number; wins: number; win_rate_pct: number; net_pnl_usd: number; profit_factor: number | null; min_trades_before_throttle: number; window_max_closed_trades?: number; ignored_data?: Record<string, number>; throttled_strategies: string[]; strategies: Record<string, RuleLearning>; attribution_note: string };
type MonitorState = {
  running: boolean; status: string; message: string; last_scan_at: number; scan_count: number;
  feed: Coin[]; positions: Position[]; history: Position[];
  events: { ts: number; text: string }[];
  source_status: Record<string, string>;
  live_tape: LiveTrade[]; live_tape_status: TapeStatus;
  strategy_lab: StrategyLab;
  entry_diagnostics?: EntryDiagnostics;
  strategy_learning?: StrategyLearning;
  paper_training?: PaperTrainingSnapshot;
  stats: { feed_count: number; open_positions: number; closed_trades: number; wins: number; win_rate: number; realized_today_usd: number; demo_starting_balance_usd: number; demo_balance_usd: number; demo_equity_usd: number; demo_available_usd: number; demo_reserved_usd: number; unrealized_pnl_usd: number; realized_total_usd: number; return_pct: number; demo_started_at: number; demo_session_id: string };
  config: { signal_strategy?: string; exit_policy?: string; learning_mode?: string; ensemble_strategies?: string[]; risk_overlay?: string; execution_verification_version?: string; scan_seconds: number; position_scan_seconds?: number; entry_score: number; max_positions: number; stop_loss_pct: number; take_profit_pct: number; trailing_pct: number; max_hold_minutes: number; min_liquidity_usd: number; trade_notional_usd: number; max_daily_loss_usd: number; starting_balance_usd: number };
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
const strategyName: Record<string, string> = { VERIFIED_FLOW_MOMENTUM: 'Verified Flow Momentum', COST_EFFICIENT_FLOW: 'Liquid Market Flow', EARLY: 'Early Runner', MOMENTUM: 'Momentum', PRECISION: 'Precision', ULTRA_PRECISION: 'Ultra Precision' };
const strategyLabels = (matches?: string[]) => Array.isArray(matches) ? matches.map(id => strategyName[id] || id).join(' · ') : '';
const lossReasonLabel = (reason: string) => reason.startsWith('STOP_LOSS') ? 'стоп загуба'
  : reason.includes('LIQUIDITY') ? 'ликвидност'
    : reason.includes('STALE') ? 'остарял пазар'
      : reason.includes('CONVICTION') ? 'слаб импулс'
        : reason.includes('TAKE_PROFIT') ? 'цел за печалба'
          : reason.includes('TRAIL') ? 'trailing изход' : reason;
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
  const [selectedLabStrategyId, setSelectedLabStrategyId] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showTestStrategies, setShowTestStrategies] = useState(false);
  const [showArchivedStrategies, setShowArchivedStrategies] = useState(false);
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
    setDetail(null);
    if (!selectedAddress) return;
    let cancelled = false;
    let pollTimer: number | undefined;
    let activeController: AbortController | undefined;
    const loadToken = async () => {
      if (cancelled) return;
      const generation = stateGeneration.current;
      try {
        if (!supabase || apiConfiguration.error) return;
        const { data: { session } } = await supabase.auth.getSession();
        const token = session?.access_token;
        if (!token) return;
        if (cancelled) return;
        const controller = new AbortController();
        activeController = controller;
        const timeout = window.setTimeout(() => controller.abort(), 4000);
        const response = await fetch(`${API}/user/token?address=${encodeURIComponent(selectedAddress)}`, {
          cache: 'no-store',
          signal: controller.signal,
          headers: { Authorization: `Bearer ${token}` },
        }).finally(() => window.clearTimeout(timeout));
        if (!response.ok) return;
        const next = await response.json() as TokenDetail;
        if (!cancelled && generation === stateGeneration.current && next.coin?.address === selectedAddress && Array.isArray(next.history)) setDetail(next);
      } catch { /* feed still works without token history */ }
      finally { if (!cancelled) pollTimer = window.setTimeout(loadToken, 2000); }
    };
    void loadToken();
    return () => { cancelled = true; activeController?.abort(); if (pollTimer) window.clearTimeout(pollTimer); };
  }, [selectedAddress]);
  const selectedTokenDetail = tokenDetailForAddress(detail, selectedAddress);
  const selectedCoin = useMemo(() => state?.feed.find(c => c.address === selectedAddress) || selectedTokenDetail?.coin || (!selectedAddress ? state?.feed[0] : null) || null, [state, selectedAddress, selectedTokenDetail]);
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (state?.feed || []).filter(coin => {
      const matchesSearch = !q || coin.symbol.toLowerCase().includes(q) || coin.name.toLowerCase().includes(q) || coin.address.toLowerCase().includes(q);
      const matchesFilter = filter === 'ALL' || filter === coin.posture || (filter === 'NEW' && (coin.ageMinutes ?? 999999) < 60) || (filter === 'BOOSTED' && coin.sources.some(s => s.includes('boost')));
      return matchesSearch && matchesFilter;
    });
  }, [state, filter, search]);

  const chartData = useMemo(() => (selectedTokenDetail?.history || []).map(p => ({ ...p, label: timeLabel(p.ts) })), [selectedTokenDetail]);
  const setupCount = state?.feed.filter(c => c.posture === 'SETUP').length || 0;
  const watchCount = state?.feed.filter(c => c.posture === 'WATCH').length || 0;

  const refreshDashboard = () => {
    setBusy(true);
    setRefreshTick(value => value + 1);
    window.setTimeout(() => setBusy(false), 500);
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
        : 'ON-CHAIN COVERAGE INCOMPLETE · FLOW-BASED ENTRIES WAIT';
  const snapshotHint = connection.receivedAt ? `Последен получен отговор: ${fullTimeLabel(connection.receivedAt)}.` : 'PAPER сметката още не е заредена.';
  const emptyStateMessage = state ? 'Няма записи в получения отговор.' : 'Данните още не са заредени от backend.';
  const promotedPortfolio = useMemo(() => promotedPaperPortfolio(state?.strategy_lab), [state]);
  const promotedPortfolioIncomplete = state?.strategy_lab?.portfolio_setup?.status === 'ACTIVE' && !promotedPortfolio;
  const portfolioBalance = promotedPortfolio?.balance ?? state?.stats.demo_balance_usd;
  const portfolioEquity = promotedPortfolio?.equity ?? state?.stats.demo_equity_usd;
  const portfolioStartingBalance = promotedPortfolio?.startingBalance ?? state?.stats.demo_starting_balance_usd;
  const portfolioAvailable = promotedPortfolio?.available ?? state?.stats.demo_available_usd;
  const portfolioReserved = promotedPortfolio?.reserved ?? state?.stats.demo_reserved_usd;
  const portfolioReturnPct = promotedPortfolio?.returnPct ?? state?.stats.return_pct;
  const portfolioRealizedPnl = promotedPortfolio?.realizedPnl ?? state?.stats.realized_total_usd;
  const portfolioUnrealizedPnl = promotedPortfolio?.unrealizedPnl ?? state?.stats.unrealized_pnl_usd;
  const portfolioOpenPositions = promotedPortfolio?.openPositions ?? state?.stats.open_positions;
  const portfolioMaxPositions = promotedPortfolio?.books.length ?? state?.config.max_positions;
  const portfolioClosedTrades = promotedPortfolio?.trades ?? state?.stats.closed_trades;
  const portfolioWinRate = promotedPortfolio?.winRate ?? state?.stats.win_rate;
  const portfolioRunning = promotedPortfolio ? state?.strategy_lab?.status === 'online' : state?.running;
  const portfolioHistory = promotedPortfolio?.history ?? (state?.history || []).map(trade => ({ ...trade, strategy_name: '' }));
  const entryRejections = Object.entries(state?.entry_diagnostics?.rejections || {}).sort((a, b) => b[1] - a[1]).slice(0, 4);
  const strategyLearningRows = Object.entries(state?.strategy_learning?.strategies || {});


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
      {promotedPortfolioIncomplete && <div role="status" className="mb-4 rounded-2xl border border-amber-400/20 bg-amber-400/[0.06] px-4 py-3 text-xs text-amber-100">Данните за главния PAPER портфейл са непълни. Показана е отделната PAPER сметка до получаване на всички избрани стратегии.</div>}
      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Metric label="Balance" value={moneyOrUnavailable(portfolioBalance)} hint={state ? `start ${moneyOrUnavailable(portfolioStartingBalance, false, 0)}` : 'Очакват се данни'} />
        <Metric label="PnL" value={moneyOrUnavailable(portfolioRealizedPnl, true)} hint={state ? `open ${moneyOrUnavailable(portfolioUnrealizedPnl, true)}` : 'Очакват се данни'} />
        <Metric label="Equity" value={`${promotedPortfolio?.valuationStale ? '~' : ''}${moneyOrUnavailable(portfolioEquity)}`} hint={promotedPortfolio?.valuationStale ? 'Последна оценка · стара котировка' : state ? percentageOrUnavailable(portfolioReturnPct) : 'Очакват се данни'} />
        <Metric label="Open" value={state ? `${portfolioOpenPositions ?? 0}/${portfolioMaxPositions ?? 0}` : '—'} hint={state ? `${moneyOrUnavailable(portfolioReserved, false, 0)} в позиции` : 'Очакват се данни'} />
        <Metric label="Win rate" value={portfolioClosedTrades ? `${(portfolioWinRate ?? 0).toFixed(1)}%` : '—'} hint={state ? `${portfolioClosedTrades ?? 0} затворени · цел 80%` : 'Очакват се данни'} />
        <Metric label="Market" value={state ? String(state.stats.feed_count) : '—'} hint={state ? `${setupCount} SETUP · ${watchCount} WATCH` : 'Очакват се данни'} />
      </section>
      <section data-testid="main-entry-learning" className="mt-4 grid gap-4 rounded-3xl border border-white/10 bg-[#0b0e11] p-4 lg:grid-cols-2">
        <div>
          <div className="flex items-center justify-between gap-3"><div><div className="text-[9px] font-black uppercase tracking-[0.16em] text-emerald-300">Отделна PAPER сметка</div><h2 className="mt-1 text-sm font-black text-white">Кандидати и причини за отказ</h2></div><span className="rounded-lg border border-white/10 px-2 py-1 text-[9px] font-black text-slate-400">{state?.entry_diagnostics?.status?.toUpperCase() ?? 'ЧАКА ДАННИ'}</span></div>
          {promotedPortfolio && <p className="mt-2 text-[10px] text-slate-500">Тези проверки са за отделната PAPER сметка. Четирите финансирани стратегии в портфейла имат собствени правила и резултати в таблицата по-долу.</p>}
          <p className="mt-2 text-[10px] leading-5 text-slate-400">{state?.entry_diagnostics?.message ?? 'Изчаква се актуална диагностика от PAPER backend.'}</p>
          <div className="mt-3 grid grid-cols-3 gap-2 text-center">
            <div className="rounded-xl border border-white/[0.06] bg-black/20 p-2"><div className="text-[8px] uppercase text-slate-600">Проверени кандидати</div><div className="mt-1 text-sm font-black text-white">{state?.entry_diagnostics?.evaluated ?? '—'}</div></div>
            <div className="rounded-xl border border-white/[0.06] bg-black/20 p-2"><div className="text-[8px] uppercase text-slate-600">Сигнали с потвърден поток</div><div className="mt-1 text-sm font-black text-cyan-200">{state?.entry_diagnostics?.signal_passed ?? '—'}</div></div>
            <div className="rounded-xl border border-white/[0.06] bg-black/20 p-2"><div className="text-[8px] uppercase text-slate-600">Проверки на цена</div><div className="mt-1 text-sm font-black text-white">{state?.entry_diagnostics?.quoted ?? '—'}</div></div>
          </div>
          {entryRejections.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{entryRejections.map(([reason, count]) => <span key={reason} className="rounded-lg border border-amber-300/10 bg-amber-300/[0.03] px-2 py-1 text-[9px] text-amber-100/80">{state?.entry_diagnostics?.reason_labels?.[reason] ?? reason}: {count}</span>)}</div>}
          {planningCostStatus(state?.entry_diagnostics?.market_cost_feasibility) && <p className="mt-3 text-[10px] leading-5 text-amber-200" data-testid="main-cost-feasibility">{planningCostStatus(state?.entry_diagnostics?.market_cost_feasibility)}</p>}
          {state?.entry_diagnostics?.quote_preparation_failures?.map((failure, index) => <p key={`${failure.symbol}-${index}`} className="mt-2 text-[10px] text-amber-200">{failure.symbol}: {quoteFailureStatus(failure.code)}</p>)}
        </div>
        <div className="border-t border-white/[0.06] pt-4 lg:border-l lg:border-t-0 lg:pl-4 lg:pt-0">
          <div className="flex items-center justify-between gap-3"><div><div className="text-[9px] font-black uppercase tracking-[0.16em] text-cyan-200">Главна PAPER сметка · обратна връзка</div><h2 className="mt-1 text-sm font-black text-white">Резултат по правила и изходи на загуба</h2></div><span className="text-[9px] text-slate-500">{state?.strategy_learning?.policy_version ?? '—'}</span></div>
          <div className="mt-2 text-[10px] text-slate-400">{state?.strategy_learning ? `${state.strategy_learning.closed_trades} валидни затворени сделки · ${state.strategy_learning.closed_trades ? `${state.strategy_learning.win_rate_pct.toFixed(1)}% печеливши` : 'успеваемост —'} · ${moneyOrUnavailable(state.strategy_learning.net_pnl_usd, true)} нето` : 'Няма отчет от текущата политика.'}</div>
          {state?.strategy_learning?.window_max_closed_trades && <div className="mt-1 text-[9px] text-slate-500">Извадка: последните до {state.strategy_learning.window_max_closed_trades} валидни затваряния за всяко правило от текущата политика.</div>}
          {Object.entries(state?.strategy_learning?.ignored_data || {}).some(([reason, count]) => reason !== 'other_policy' && count > 0) && <div className="mt-1 text-[9px] text-amber-200">Изключени невалидни или повторени данни. Отчетът използва само проверени затваряния.</div>}
          {state?.strategy_learning?.closed_trades === 0 && <div className="mt-2 text-[9px] leading-5 text-slate-500">Новата политика започва с чиста извадка. Причините ще се записват след затваряне; правило се поставя на пауза само след поне {state.strategy_learning.min_trades_before_throttle} сделки и слаб нетен резултат.</div>}
          <div className="mt-3 space-y-1.5">{strategyLearningRows.map(([name, row]) => <div key={name} className="rounded-lg bg-black/20 px-2.5 py-2 text-[9px]"><div className="flex items-center justify-between gap-2"><span className="font-bold text-slate-300">{strategyName[name] || name}</span><span className="text-slate-500">{row.closed_trades} сделки · {row.closed_trades ? `${row.win_rate_pct.toFixed(1)}%` : '—'} · <b className={row.net_pnl_usd >= 0 ? 'text-emerald-300' : 'text-red-300'}>{moneyOrUnavailable(row.net_pnl_usd, true)}</b>{row.throttled ? ' · пауза' : ''}</span></div>{Object.keys(row.loss_reasons || {}).length > 0 && <div className="mt-1 text-slate-600">Изходи на губещи сделки: {Object.entries(row.loss_reasons).map(([reason, count]) => `${lossReasonLabel(reason)} ×${count}`).join(' · ')}</div>}</div>)}</div>
          {state?.strategy_learning?.attribution_note && <div className="mt-2 text-[8px] leading-4 text-slate-600">Една сделка може да съвпада с няколко правила. Причината за изход показва задействания механизъм; тя не доказва защо входът е загубил или че следващият ще спечели. Правило на пауза изисква нови валидни резултати от съвпадащи входове или преглед на политиката.</div>}
        </div>
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
                <div className="bg-[#0b0e11] p-3"><div className="text-[8px] font-black uppercase text-slate-700">BUY 60s</div><div className="mt-1 text-sm font-black text-emerald-300">{selectedTokenDetail?.flow ? fmtMoney(selectedTokenDetail.flow.buy_usd) : '—'}</div></div>
                <div className="bg-[#0b0e11] p-3"><div className="text-[8px] font-black uppercase text-slate-700">SELL 60s</div><div className="mt-1 text-sm font-black text-red-300">{selectedTokenDetail?.flow ? fmtMoney(selectedTokenDetail.flow.sell_usd) : '—'}</div></div>
                <div className="bg-[#0b0e11] p-3"><div className="text-[8px] font-black uppercase text-slate-700">BUY/SELL</div><div className="mt-1 text-sm font-black text-white">{selectedTokenDetail?.flow ? `${selectedTokenDetail.flow.buy_sell_usd_ratio.toFixed(2)}x` : '—'}</div></div>
                <div className="bg-[#0b0e11] p-3"><div className="text-[8px] font-black uppercase text-slate-700">WALLETS 60s</div><div className="mt-1 text-sm font-black text-white">{selectedTokenDetail?.flow?.unique_wallets ?? '—'}</div></div>
              </div>
              <div className="max-h-[280px] overflow-y-auto">
                {(selectedTokenDetail?.live_tape || []).length ? (selectedTokenDetail?.live_tape || []).slice(0, 40).map(tx => <a key={tx.signature} href={`https://solscan.io/tx/${tx.signature}`} target="_blank" rel="noreferrer" className="grid grid-cols-[62px_48px_minmax(70px,1fr)_92px_86px] items-center gap-2 border-b border-white/[0.05] px-4 py-2.5 text-[9px] hover:bg-white/[0.025]">
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
            <div className="flex items-start justify-between gap-3">
              <div><div className="text-[9px] font-black uppercase tracking-[0.18em] text-emerald-300">NEO AUTO BOT</div><h2 className="mt-1 text-lg font-black text-white">PAPER портфейл</h2></div>
              <div className={`rounded-lg border px-2 py-1 text-[9px] font-black ${connected && portfolioRunning ? 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300' : 'border-white/10 bg-white/[0.03] text-slate-500'}`}>{connected ? portfolioRunning ? 'RUNNING' : 'PAUSED' : connectionStatus}</div>
            </div>
            <div className="mt-4 rounded-2xl border border-emerald-400/15 bg-emerald-400/[0.045] p-3">
              <div className="flex items-end justify-between gap-3">
                <div><div className="text-[8px] font-black uppercase tracking-[0.14em] text-emerald-300/70">BALANCE</div><div className="mt-1 text-2xl font-black text-white">{moneyOrUnavailable(portfolioBalance)}</div></div>
                <div className={`text-sm font-black ${(portfolioReturnPct ?? 0) >= 0 ? 'text-emerald-300' : state ? 'text-red-300' : 'text-slate-500'}`}>{percentageOrUnavailable(portfolioReturnPct)}</div>
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2 border-t border-white/[0.06] pt-3 text-[9px]">
                <div><div className="text-slate-700">EQUITY</div><div className={`mt-0.5 font-black ${promotedPortfolio?.valuationStale ? 'text-amber-200' : 'text-white'}`}>{promotedPortfolio?.valuationStale ? '~' : ''}{moneyOrUnavailable(portfolioEquity)}</div></div>
                <div><div className="text-slate-700">FREE</div><div className="mt-0.5 font-black text-white">{moneyOrUnavailable(portfolioAvailable)}</div></div>
                <div><div className="text-slate-700">IN POS.</div><div className="mt-0.5 font-black text-white">{moneyOrUnavailable(portfolioReserved)}</div></div>
              </div>
            </div>
            {promotedPortfolio?.valuationStale && <div className="mt-2 text-[9px] text-amber-200">Equity и отвореният PnL включват последна оценка със стара котировка.</div>}
            <div className="mt-3 flex items-center justify-between gap-3 text-[9px] text-slate-500">
              <span>{promotedPortfolio ? `${promotedPortfolio.books.length} главни стратегии` : `${state?.config.ensemble_strategies?.length ?? 0} стратегии`}</span>
              <span>{portfolioClosedTrades ?? 0} затворени · {portfolioClosedTrades ? `${(portfolioWinRate ?? 0).toFixed(0)}% WR` : 'WR —'}</span>
            </div>
          </div>

          <div className="rounded-3xl border border-white/10 bg-[#0b0e11] p-4">
            <div className="flex items-center justify-between"><h3 className="text-base font-black text-white">Отворени позиции</h3><WalletCards className="h-4 w-4 text-emerald-300" /></div>
            <div className="mt-4 space-y-2">
              {promotedPortfolio ? <>
                {promotedPortfolio.openPositions === 0 && <div className="rounded-xl border border-dashed border-white/[0.08] p-4 text-center text-[10px] leading-5 text-slate-600">Няма отворени позиции.</div>}
                {promotedPortfolio.books.filter(({ book }) => Boolean(book.position)).map(({ id, book }) => {
                  const position = book.position!;
                  return <div key={`${id}-${position.opened_at}`} className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-3">
                    <button onClick={() => setSelectedAddress(position.address)} className="w-full text-left hover:opacity-80"><div className="flex items-center justify-between gap-2"><div><div className="text-xs font-black text-white">${position.symbol} · {book.name}</div><div className="mt-0.5 text-[9px] text-slate-600">вход {fmtPrice(position.execution_entry_price ?? position.entry_price ?? 0)} · ${position.notional_usd.toFixed(0)}</div></div><div className={`text-sm font-black ${position.quote_status === 'fresh' ? (position.pnl_pct >= 0 ? 'text-emerald-300' : 'text-red-300') : 'text-amber-200'}`}>{position.quote_status === 'fresh' ? `${position.pnl_pct >= 0 ? '+' : ''}${position.pnl_pct.toFixed(2)}%` : 'STALE'}</div></div></button>
                    <div className="mt-2 flex items-center justify-between text-[9px] text-slate-600"><span>{position.open_pnl_usd != null ? `${position.open_pnl_usd >= 0 ? '+' : ''}$${position.open_pnl_usd.toFixed(2)}` : 'PnL —'}</span><span>{Math.max(0, Math.round((Date.now() - position.opened_at) / 60000))}m open</span></div>
                    {position.pairAddress && <a href={`https://dexscreener.com/solana/${encodeURIComponent(position.pairAddress)}`} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-1 rounded-lg border border-cyan-300/20 px-2 py-1 text-[9px] font-black text-cyan-200">DEX Screener <ExternalLink className="h-3 w-3" /></a>}
                  </div>;
                })}
              </> : <>
                {(state?.positions || []).length === 0 && <div className="rounded-xl border border-dashed border-white/[0.08] p-4 text-center text-[10px] leading-5 text-slate-600">{state ? 'Няма отворени позиции.' : 'Данните още не са заредени.'}</div>}
                {state?.positions.map(position => <div key={position.id} className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-3">
                  <button onClick={() => setSelectedAddress(position.address)} className="w-full text-left hover:opacity-80"><div className="flex items-center justify-between gap-2"><div><div className="text-xs font-black text-white">${position.symbol}</div><div className="mt-0.5 text-[9px] text-slate-600">#{position.trade_no ?? '—'} · вход {fmtPrice(position.execution_entry_price ?? position.entry_price)} · ${position.notional_usd.toFixed(0)}</div></div><div className={`text-sm font-black ${position.pnl_pct >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{position.pnl_pct >= 0 ? '+' : ''}{position.pnl_pct.toFixed(2)}%</div></div></button>
                  {!!position.strategy_matches?.length && <div className="mt-2 text-[9px] text-cyan-200/80">Сигнали: {strategyLabels(position.strategy_matches)}</div>}
                  <div className="mt-2 flex items-center justify-between text-[9px] text-slate-600"><span>{position.pnl_usd >= 0 ? '+' : ''}${position.pnl_usd.toFixed(2)} · Score {position.current_score?.toFixed(0) ?? position.score.toFixed(0)}</span><span>{Math.max(0, Math.round((Date.now() - position.opened_at) / 60000))}m open</span></div>
                  {(position.dex_url || position.pairAddress) && <a href={position.dex_url || `https://dexscreener.com/solana/${encodeURIComponent(position.pairAddress)}`} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-1 rounded-lg border border-cyan-300/20 px-2 py-1 text-[9px] font-black text-cyan-200">DEX Screener <ExternalLink className="h-3 w-3" /></a>}
                </div>)}
              </>}
            </div>
          </div>
        </aside>
      </section>

      <section className="mt-4 overflow-hidden rounded-3xl border border-cyan-400/15 bg-[#0b0e11]">
        <div className="flex flex-col gap-3 border-b border-white/[0.07] p-4 sm:flex-row sm:items-center sm:justify-between">
          <div><div className="text-[9px] font-black uppercase tracking-[0.18em] text-cyan-300">PAPER STRATEGIES</div><h2 className="mt-1 text-lg font-black text-white">Стратегии</h2><div className="mt-1 text-[9px] text-slate-600">Главните стратегии са най-отгоре. Натисни стратегия за сделките ѝ.</div></div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => setShowAdvanced(value => !value)} className={`rounded-lg border px-2.5 py-1.5 text-[9px] font-black ${showAdvanced ? 'border-cyan-300/30 bg-cyan-300/10 text-cyan-100' : 'border-white/10 text-slate-500 hover:text-white'}`}>{showAdvanced ? 'СКРИЙ ДЕТАЙЛИ' : 'РАЗШИРЕНИ ДАННИ'}</button>
            <div className={`rounded-lg border px-2.5 py-1.5 text-[9px] font-black ${connected && state?.strategy_lab?.status === 'online' ? 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300' : 'border-amber-400/20 bg-amber-400/10 text-amber-200'}`}>{connected ? (state?.strategy_lab?.status || 'UNKNOWN').toUpperCase() : connectionLabel}</div>
          </div>
        </div>
        {showAdvanced && <div className="space-y-3 border-b border-white/[0.06] p-4">
          <div data-testid="lab-integrity-warning" className="rounded-xl border border-amber-400/20 bg-amber-400/[0.04] px-4 py-3 text-[10px] leading-5 text-amber-100">Историческите Lab данни съдържат известни несъответствия. Новите входове проверяват точния pool преди симулация.</div>
          {state?.strategy_lab?.portfolio_setup?.version && state.strategy_lab.portfolio_setup.status !== 'UNCONFIGURED' && <div data-testid="lab-promoted-paper-cohort" className="rounded-xl border border-emerald-400/15 bg-emerald-400/[0.03] px-4 py-3 text-[10px] leading-5 text-slate-400">
            Главен PAPER капитал: <b className="text-white">${state.strategy_lab.portfolio_setup.total_allocated_capital_usd?.toFixed(0) ?? '1,000'}</b> · {state.strategy_lab.portfolio_setup.strategies?.length ?? 0} стратегии · {state.strategy_lab.portfolio_setup.historical_simulations ?? 0} исторически симулации.
            {state.strategy_lab.portfolio_setup.promotion_error && <div className="mt-1 text-amber-200">{state.strategy_lab.portfolio_setup.promotion_error}</div>}
          </div>}
          <div className="rounded-xl border border-sky-400/15 bg-sky-400/[0.03] px-4 py-3 text-[10px] leading-5 text-slate-400">Strategy Lab проверява точния pool преди PAPER вход и сверява цената с независим източник. DEX такси, price impact, slippage, забавяне и мрежов разход остават моделирани. Котировката не е изпълнена транзакция; стара цена се обозначава и не задейства изход.</div>
          <div className="rounded-xl border border-amber-400/15 bg-amber-400/[0.03] px-4 py-3 text-[10px] leading-5 text-amber-100/75">Финансираните PAPER стратегии изискват пресни потвърдени сделки от точния pool, пълна проверка за безопасност и моделиран разход до 1.5% на вход/изход. Този филтър е нов и доходността му още не е доказана; PAPER резултатите не гарантират печалба.</div>
          {state?.strategy_lab?.astra && <AstraBrainPanel data={state.strategy_lab.astra} />}
          <LabPairedPanel data={state?.strategy_lab?.paired} />
          <div className="rounded-xl border border-white/[0.07] bg-black/20 p-3">
            <div className="text-[9px] font-black uppercase tracking-[0.14em] text-slate-600">Последна активност</div>
            <div className="mt-2 space-y-2">{(state?.events || []).slice(0, 5).map(event => <div key={`${event.ts}-${event.text}`} className="flex items-start justify-between gap-3 text-[9px]"><span className="text-slate-400">{event.text}</span><span className="shrink-0 text-slate-700">{timeLabel(event.ts)}</span></div>)}</div>
          </div>
          <div data-testid="execution-integrity" className="rounded-xl border border-white/[0.07] bg-black/20 p-3 text-[10px] leading-5 text-slate-500">
            {state ? (state.config.exit_policy === 'adaptive'
              ? <>Отделна PAPER сметка: сигнал {state.config.signal_strategy ?? '—'} · стоп −{state.config.stop_loss_pct}% нето · адаптивен изход по убеденост ({state.config.learning_mode ?? 'ADAPTIVE_CONTEXT_HOLD'}): RUNNER/STRONG без фиксирана цел, NORMAL +20%, CAUTIOUS +14%, WEAK +8% нето; trailing 3–7%; max hold 4–60 min, абсолютно 120 min · {state.config.max_positions} позиция.</>
              : <>Отделна PAPER сметка: сигнал {state.config.signal_strategy ?? '—'} · стоп −{state.config.stop_loss_pct}% нето · цел +{state.config.take_profit_pct}% нето · trailing {state.config.trailing_pct}% · max hold {state.config.max_hold_minutes}m.</>) : 'Настройките още не са заредени.'}
          </div>
        </div>}
        <div className="border-b border-white/[0.06] px-4 py-3 text-[10px] leading-5 text-slate-400">
          <p>Първо са четирите стратегии с отделен PAPER капитал. Това е разпределение на сметката, а не класация на доказани печалби. При нула затворени сделки доходността още не е проверена.</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button type="button" aria-pressed={showTestStrategies} onClick={() => setShowTestStrategies(value => !value)} className="rounded-lg border border-white/10 px-3 py-1.5 text-slate-300">{showTestStrategies ? 'Скрий' : 'Покажи'} тестови кандидати ({partitionLabStrategies(Object.values(state?.strategy_lab?.books || {})).research.length})</button>
            <button type="button" aria-pressed={showArchivedStrategies} onClick={() => setShowArchivedStrategies(value => !value)} className="rounded-lg border border-amber-300/20 px-3 py-1.5 text-amber-200">{showArchivedStrategies ? 'Скрий' : 'Покажи'} архив ({partitionLabStrategies(Object.values(state?.strategy_lab?.books || {})).archived.length})</button>
          </div>
          {showTestStrategies && <p className="mt-2">Тестовите кандидати са непотвърдени експерименти с отделни симулирани сметки. Подредбата е по процентен резултат, а малката извадка не доказва устойчивост.</p>}
          {showArchivedStrategies && <p className="mt-2 text-amber-100/80">Архивираните стратегии не отварят нови сделки. Историята и балансите са запазени; регистрираните стратегии продължават да управляват съществуващите си позиции.</p>}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-left">
            <thead><tr className="border-b border-white/[0.06] text-[8px] font-black uppercase tracking-[0.14em] text-slate-700"><th className="px-4 py-3">Стратегия</th><th className="px-4 py-3">Баланс</th><th className="px-4 py-3">PnL</th><th className="px-4 py-3">Сделки</th><th className="px-4 py-3">Win rate</th><th className="px-4 py-3">Позиция</th></tr></thead>
            <tbody>
              {(() => {
                const groups = partitionLabStrategies(Object.values(state?.strategy_lab?.books || {}), state?.strategy_lab?.stats);
                return [...groups.funded, ...(showTestStrategies ? groups.research : []), ...(showArchivedStrategies ? groups.archived : [])];
              })().map(book => {
                const st = state?.strategy_lab?.stats?.[book.id];
                const pnl = st?.total_pnl ?? (st ? st.equity - book.starting_balance : book.balance + (book.position?.open_pnl_usd ?? 0) - book.starting_balance);
                const realizedPnl = st?.realized_pnl ?? (book.balance - book.starting_balance);
                const openPnl = st?.unrealized_pnl ?? book.position?.open_pnl_usd ?? 0;
                const diagnostics = book.entry_diagnostics;
                const inactive = book.runtime_compatibility?.status === 'preserved_inactive';
                const retired = isArchivedStrategy(book) && !inactive;
                const expanded = selectedLabStrategyId === book.id;
                const trades = book.history || [];
                const referencePair = book.position?.pairAddress || trades[0]?.pairAddress;
                const dexUrl = referencePair ? `https://dexscreener.com/solana/${encodeURIComponent(referencePair)}` : '';
                const bookDexButton = dexUrl
                  ? <a href={dexUrl} target="_blank" rel="noopener noreferrer" onClick={event => event.stopPropagation()} aria-label={`Отвори ${book.name} в DexScreener`} className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1.5 text-[8px] font-black text-slate-400 hover:border-cyan-300/25 hover:text-cyan-200">DEX <ExternalLink className="h-3 w-3" /></a>
                  : <span title="Ще има адрес след първа изпълнена PAPER позиция" className="inline-flex items-center gap-1 rounded-lg border border-white/[0.05] px-2 py-1.5 text-[8px] font-black text-slate-700">DEX · няма pool</span>;
                return <Fragment key={book.id}>
                <tr className={`border-b border-white/[0.04] text-xs hover:bg-white/[0.02] ${expanded ? 'bg-cyan-400/[0.025]' : ''}`}>
                  <td className="px-4 py-3"><button type="button" aria-expanded={expanded} onClick={() => setSelectedLabStrategyId(expanded ? '' : book.id)} className="text-left"><div className="font-black text-white">{book.name}<span className={`ml-2 rounded border px-1.5 py-0.5 text-[8px] font-black ${retired ? 'border-amber-300/20 text-amber-200' : book.portfolio_group === 'PROMOTED_PAPER' ? 'border-emerald-300/20 text-emerald-200' : book.portfolio_group === 'PROMOTION_DRAINING' ? 'border-amber-300/20 text-amber-200' : 'border-white/[0.06] text-slate-600'}`}>{retired ? 'АРХИВ' : book.portfolio_group === 'PROMOTED_PAPER' ? `PAPER $${book.allocation_usd ?? book.starting_balance}` : book.portfolio_group === 'PROMOTION_DRAINING' ? 'ПОДГОТОВКА' : 'ТЕСТ'}</span><span className="ml-2 text-[9px] font-medium text-cyan-200/70">{expanded ? 'сгъни · ' : ''}затворени · {st?.trades ?? trades.length}</span></div></button>{retired && <div className="mt-1 text-[9px] text-amber-200">СПРЯНА · повтарящи се загуби; историята е запазена.</div>}{inactive && <div className="mt-1 text-[9px] text-amber-200">НЕАКТИВНА · историята е запазена; отворените оценки не се обновяват.</div>}{book.id === 'FLOW_MOMENTUM_SCALE_OUT' && <div className="mt-1 text-[9px] text-amber-200/80">Общ изход −3/+10; близките входове могат да дадат еднакви сделки.</div>}</td>
                  <td className="px-4 py-3"><div className="font-black text-white">${book.balance.toFixed(2)}</div><div className={`mt-1 text-[9px] ${st?.valuation_stale ? 'text-amber-200' : 'text-slate-600'}`}>equity {st?.valuation_stale ? '~' : ''}${(st?.equity ?? book.balance).toFixed(2)}</div></td>
                  <td className={`px-4 py-3 font-black ${pnl >= 0 ? 'text-emerald-300' : 'text-red-300'}`}>{st?.valuation_stale ? '~' : ''}{moneyOrUnavailable(pnl, true)}<div className="mt-1 text-[9px]">{percentageOrUnavailable(st?.return_pct)}</div></td>
                  <td className="px-4 py-3 text-slate-400">затворени · {st?.trades ?? 0}<div className={`mt-1 text-[9px] ${book.position ? 'text-cyan-200' : 'text-slate-600'}`}>отворени · {book.position ? 1 : 0}</div><div className="mt-1 text-[9px] text-slate-700">{st?.wins ?? 0}W / {st?.losses ?? 0}L</div></td>
                  <td className="px-4 py-3 font-black text-white">{st?.trades ? `${st.win_rate.toFixed(1)}%` : '—'}<div className="mt-1 text-[9px] font-normal text-slate-600">PF {st?.profit_factor != null ? st.profit_factor.toFixed(2) : st?.wins && !st.losses ? '∞' : '—'}</div></td>
                  <td className="px-4 py-3"><div className="flex items-center gap-2">{book.position ? <button onClick={() => setSelectedAddress(book.position!.address)} className="rounded-xl border border-cyan-400/15 bg-cyan-400/[0.05] px-3 py-2 text-left"><div className="font-black text-cyan-200">${book.position.symbol}</div><div className={`mt-1 text-[9px] font-black ${book.position.quote_status === 'fresh' ? ((book.position.pnl_pct || 0) >= 0 ? 'text-emerald-300' : 'text-red-300') : 'text-amber-200'}`}>{book.position.quote_status === 'fresh' ? `${(book.position.pnl_pct || 0) >= 0 ? '+' : ''}${(book.position.pnl_pct || 0).toFixed(2)}%` : 'КОТИРОВКА СТАРА'} · ${book.position.notional_usd.toFixed(0)}</div></button> : <LabEntryStatus book={book} backendAvailable={connected} />}{bookDexButton}</div></td>
                </tr>
                {expanded && <tr className="border-b border-cyan-400/10 bg-black/20"><td colSpan={6} className="px-4 py-4">
                  <div className="mb-3 text-[10px] text-slate-400">Нетен PAPER PnL: реализиран {moneyOrUnavailable(realizedPnl, true)} · отворен {moneyOrUnavailable(openPnl, true)}.{book.id === 'MOMENTUM_RUSH_BRAIN' && <span className="ml-2 text-amber-200">Цел 80% успеваемост · експериментът още трябва да я докаже.</span>}</div>
                  {retired && <div className="mb-3 rounded-lg border border-amber-300/20 p-3 text-[10px] text-amber-100">Новите входове са спрени след повтарящ се отрицателен нетен резултат. Оценени сделки: {book.strategy_lifecycle?.evidence?.closed_trades ?? '—'} · печеливши: {book.strategy_lifecycle?.evidence?.wins ?? '—'} · нетен PnL: {moneyOrUnavailable(book.strategy_lifecycle?.evidence?.net_pnl_usd, true)}. Този архив запазва загубите видими; това не доказва, че останалите стратегии ще печелят.</div>}
                  {diagnostics && <div className="mb-3 text-[9px] leading-5 text-slate-500">Сигнали {diagnostics.signal_candidates ?? 0} · чакат цена {diagnostics.price_crosscheck_pending ?? 0} · лоша цена {diagnostics.price_verification_rejected ?? 0} · разходи {diagnostics.cost_rejected ?? 0}{(diagnostics.flow_missing_candidates ?? 0) > 0 ? ` · чака проверен поток ${diagnostics.flow_missing_candidates}` : ''}{diagnostics.promoted_policy_version ? ` · проверен поток отказан ${diagnostics.promoted_flow_rejected ?? 0} · safety отказан ${diagnostics.promoted_safety_rejected ?? 0} · строг лимит разходи ${diagnostics.promoted_max_entry_roundtrip_cost_pct?.toFixed(2) ?? '—'}%` : ''}{book.id === 'MOMENTUM_RUSH_BRAIN' ? ` · чакат наблюдения ${diagnostics.temporal_warmup_candidates ?? 0} · филтър ${diagnostics.brain_rejected_candidates ?? 0} · риск ${diagnostics.risk_rejected_candidates ?? 0}` : ''}{diagnostics.promoted_policy_version && diagnostics.blocked_reason ? ` · причина: ${diagnostics.blocked_reason}` : ''}</div>}
                  {planningCostStatus(diagnostics?.promoted_cost_feasibility) && <p className="mb-3 text-[10px] leading-5 text-amber-200">{planningCostStatus(diagnostics?.promoted_cost_feasibility)}</p>}
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

      <PaperPortfolioHistory trades={portfolioHistory} total={portfolioClosedTrades} loaded={Boolean(state)} promoted={Boolean(promotedPortfolio)} onSelectAddress={setSelectedAddress} formatPrice={fmtPrice} formatTime={fullTimeLabel} />

      {state?.paper_training ? <PaperTrainingPanel data={state.paper_training} /> : <section className="mt-4 rounded-2xl border border-amber-400/20 bg-amber-400/[0.04] p-4 text-xs text-amber-100">{state ? 'Backend не е предоставил данни за обучителния PAPER режим. Активната версия и резултатите още не са потвърдени.' : 'Данните за обучителния PAPER режим още не са заредени от backend.'}</section>}
      <footer className="mt-5 flex flex-col justify-between gap-2 border-t border-white/[0.06] py-5 text-[9px] leading-4 text-slate-700 sm:flex-row"><div>NEO Meme Coins · live Solana market monitoring · isolated account engine</div><div className="max-w-2xl sm:text-right">Paper режимът е симулация. Meme coins са високорискови; score-ът е филтър за наблюдение, не обещание за печалба.</div></footer>
    </main>
  </div>;
}

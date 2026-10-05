export interface MemeCoin {
  id: string;
  name: string;
  ticker: string;
  contractAddress: string;
  launchTime: string;
  priceUsd: number;
  priceChangePercent: number;
  fdv?: number;
  liquidityLock: boolean;
  hasTwitter: boolean;
  hasWebsite: boolean;
  dexPaid: boolean;
  scamScore: number | null;
  scamStatus: 'SAFE' | 'WARNING' | 'RUGGED' | 'SCAM' | 'UNVERIFIED';
  ownerWallets: Array<{ address: string; percentage: number; prevTradeWinRate: number }>;
  twitterStats?: {
    followers: number;
    averageLikes: number;
    isBotLiking: boolean;
    sentimentText: string;
  };
  priceDirection?: 'up' | 'down' | 'flat';
  holdersCount?: number;
}

export interface EliteTrader {
  id: string;
  name: string;
  alias: string;
  winRate: number;
  weeklyProfitSol: number;
  address: string;
  status: 'IDLE' | 'BUYING' | 'SELLING';
}

export interface TradeLog {
  id: string;
  timestamp: string;
  coinTicker: string;
  coinName: string;
  contractAddress: string;
  type: 'BUY' | 'SELL_PROFIT' | 'SELL_STOP_LOSS' | 'FRONT_RUN_BUY' | 'FRONT_RUN_SELL' | 'RUG_EXIT';
  amountSol: number;
  priceEntry: number;
  priceExit: number | null;
  entryFdv?: number;
  exitFdv?: number | null;
  returnPercent: number | null;
  solProfit: number | null;
  status: 'ACTIVE' | 'CLOSED';
  mode: 'AUTOPILOT_TWITTER' | 'COPY_TRADE' | 'MANUAL_SIGNAL';
  reason: string;
  priceDirection?: 'up' | 'down' | 'flat';
  secondsHeld?: number;
  holderConcentration?: number;
  devSellPressure?: 'LOW' | 'MEDIUM' | 'HIGH';
  targetHoldingDuration?: number;
  winRateSignal?: number;
  holdersCount?: number;
  highestReturnPercent?: number;
  priceHistory?: number[];
  solscanSignature?: string;
}

export interface BotSettings {
  autoTrade: boolean;
  stopLossPercent: number;
  takeProfitPercent: number;
  tradeSizeSol: number;
  telegramEnabled: boolean;
  phantomConnected: boolean;
  walletBalanceSol: number;
  realWalletAddress?: string;
  tradingMode?: 'DEMO' | 'LIVE';
  hotWalletAddress?: string;
}

export interface DebugProof {
  timestamp: string;
  endpoint: string;
  tokenAddress: string;
  rawResponse: string;
  uiPriceUsd: number;
  apiPriceUsd: number;
  isPerfectSync: boolean;
}


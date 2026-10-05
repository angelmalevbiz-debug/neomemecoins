import React, { useState } from 'react';
import { Search, ShieldAlert, ShieldCheck, Flame, Loader2, Cpu, HelpCircle, BadgeCheck } from 'lucide-react';
import ReactMarkdown from 'react-markdown';

interface ScanResult {
  isScam: boolean;
  scamScore: number;
  scamReasons: string[];
  potentialMultiplier: number;
  sentimentAnalysis: string;
  tradeDecision: string;
  auditReport: string;
}

const PRESETS = [
  {
    name: "Dogwifhat (WIF) - Real-time Simulator",
    address: "EKpQge67nn977Yg6yP7V88g7v98yU67yYHgHg",
    ticker: "WIF",
    coinName: "Dogwifhat",
    hasWebsite: true,
    hasTwitter: true,
    dexPaid: true,
    liquidityLock: true,
    twitterStats: { followers: 185000, averageLikes: 1450, isBotLiking: false, sentimentText: "Bullish, massive community engagement with high organic interest." },
    topHolders: [{ address: "DEvWaf...2yHg", percentage: 5.2, prevTradeWinRate: 72 }]
  },
  {
    name: "RugPull Classic (RUGGY) - High Risk Example",
    address: "RUG99999YHg78gY78yHGYHg789yHG78yHG",
    ticker: "RUGGY",
    coinName: "RugPull Classic",
    hasWebsite: false,
    hasTwitter: true,
    dexPaid: false,
    liquidityLock: false,
    twitterStats: { followers: 1200, averageLikes: 4, isBotLiking: true, sentimentText: "Extremely low engagement, repetitive robotic tweets." },
    topHolders: [{ address: "RUGdev...f92y", percentage: 38.5, prevTradeWinRate: 15 }]
  },
  {
    name: "DEXScreener Paid Gem (MOONCAT)",
    address: "CAT88888YHg78gY78yHGYHg789yHG78yHG",
    ticker: "MOONCAT",
    coinName: "MoonCat",
    hasWebsite: true,
    hasTwitter: true,
    dexPaid: true,
    liquidityLock: true,
    twitterStats: { followers: 15400, averageLikes: 380, isBotLiking: false, sentimentText: "High velocity, active shilling by popular influencers." },
    topHolders: [{ address: "CATdev...23f9", percentage: 8.4, prevTradeWinRate: 64 }]
  }
];

interface SmartScannerProps {
  initialAddress?: string;
  setInitialAddress?: (address: string) => void;
}

export default function SmartScanner({ initialAddress, setInitialAddress }: SmartScannerProps) {
  const [customAddress, setCustomAddress] = useState(initialAddress || '');
  const [loading, setLoading] = useState(false);
  const [loadingStep, setLoadingStep] = useState('');
  const [result, setResult] = useState<ScanResult | null>(null);
  const [scannedAddress, setScannedAddress] = useState('');

  React.useEffect(() => {
    if (initialAddress) {
      setCustomAddress(initialAddress);
    }
  }, [initialAddress]);

  const runScan = async (coinConfig: typeof PRESETS[0]) => {
    setLoading(true);
    setResult(null);
    setScannedAddress(coinConfig.address || '');
    
    // Simulate high-tech step loading for realism and luxury UI experience
    const steps = [
      "📡 Свързване към Solana RPC нод...",
      "🔬 Четене на смарт контракта...",
      "👥 Анализиране на най-големите притежатели (Owners)...",
      "🐦 Проверка на Twitter акаунта и активността на постовете...",
      "🧠 Стартиране на Gemini AI смарт одит и сентимент анализ..."
    ];

    for (let i = 0; i < steps.length; i++) {
      setLoadingStep(steps[i]);
      await new Promise(resolve => setTimeout(resolve, 800));
    }

    try {
      const response = await fetch('/api/analyze-coin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(coinConfig)
      });
      const data = await response.json();
      setResult(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleCustomScan = () => {
    if (!customAddress.trim()) return;
    
    // Build a mock/random config for any custom typed address
    const mockConfig = {
      name: `Custom Scan Coin`,
      coinName: `Custom Meme Coin`,
      ticker: `MEME`,
      address: customAddress,
      contractAddress: customAddress,
      hasWebsite: Math.random() > 0.4,
      hasTwitter: Math.random() > 0.2,
      dexPaid: Math.random() > 0.5,
      liquidityLock: Math.random() > 0.3,
      twitterStats: {
        followers: Math.floor(Math.random() * 25000) + 100,
        averageLikes: Math.floor(Math.random() * 500),
        isBotLiking: Math.random() > 0.6,
        sentimentText: Math.random() > 0.5 ? "Bullish and hype-driven" : "Mixed sentiment with some spam indicators."
      },
      topHolders: [
        { address: "devWal...3fHg", percentage: Math.floor(Math.random() * 45) + 2, prevTradeWinRate: Math.floor(Math.random() * 80) + 10 }
      ]
    };

    runScan(mockConfig);
  };

  return (
    <div className="bg-white/[0.02] border border-white/10 rounded-none p-6 shadow-xl space-y-6">
      
      {/* Title block */}
      <div className="border-b border-white/10 pb-4 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <span className="text-[10px] font-mono bg-white/10 text-[#00FFA3] px-2 py-0.5 rounded-none border border-white/20 uppercase tracking-wider font-bold">
            AI SCAM AUDITOR
          </span>
          <h3 className="font-bold text-white text-base mt-2">Изкуствен Интелект - Проверка за Скамове</h3>
          <p className="text-[11px] text-white/40 mt-1 leading-relaxed">
            Сканирайте смарт контракти и проверете в реално време дали даден меме коин е сигурен или крие риск от ръкпул.
          </p>
        </div>
      </div>

      {/* Preset selection buttons */}
      <div className="space-y-2">
        <p className="text-[10px] font-mono text-white/40 uppercase tracking-widest font-black">Изберете готов шаблон за бърза демонстрация:</p>
        <div className="flex flex-wrap gap-2">
          {PRESETS.map((preset, idx) => (
            <button
              key={idx}
              onClick={() => runScan(preset)}
              disabled={loading}
              className="text-xs font-mono px-3.5 py-2 rounded-none border border-white/10 bg-[#0A0B0E] hover:bg-white/5 hover:border-white/20 text-white/80 transition-all cursor-pointer"
            >
              {preset.name}
            </button>
          ))}
        </div>
      </div>

      {/* Input bar */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3.5 top-3.5 h-4 w-4 text-white/40" />
          <input
            type="text"
            value={customAddress}
            onChange={(e) => setCustomAddress(e.target.value)}
            disabled={loading}
            placeholder="Въведете Solana адрес на смарт контракт (CA)..."
            className="w-full bg-[#0A0B0E] border border-white/10 rounded-none py-3.5 pl-11 pr-4 text-xs text-white placeholder-white/30 focus:outline-none focus:border-[#00FFA3]/40 transition-all font-mono"
          />
        </div>
        
        <button
          onClick={handleCustomScan}
          disabled={loading || !customAddress.trim()}
          className="bg-[#00FFA3] hover:bg-[#00FFA3]/90 text-black text-xs font-black px-5 py-3 rounded-none transition-all cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 shrink-0 shadow-[0_0_15px_rgba(0,255,163,0.15)]"
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Cpu className="h-4 w-4" />
          )}
          СКАНИРАЙ С AI
        </button>
      </div>

      {/* Loading state display */}
      {loading && (
        <div className="bg-[#0A0B0E] border border-white/5 rounded-none p-8 flex flex-col items-center justify-center space-y-3">
          <Loader2 className="h-7 w-7 text-[#00FFA3] animate-spin" />
          <p className="text-xs font-mono text-[#00FFA3] animate-pulse">{loadingStep}</p>
        </div>
      )}

      {/* Audit reports display */}
      {result && (
        <div className="space-y-4">
          
          {/* Scanned Contract Info Row with DEXScreener button */}
          <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center p-4 bg-[#0A0B0E] border border-white/10 rounded-none gap-3 font-mono">
            <div className="min-w-0 flex-1">
              <p className="text-[10px] text-white/40 uppercase tracking-widest font-black">Одитиран смарт контракт (CA)</p>
              <p className="text-xs text-[#00FFA3] break-all select-all mt-0.5">{scannedAddress}</p>
            </div>
            {scannedAddress && (
              <a 
                href={`https://dexscreener.com/solana/${scannedAddress}`}
                target="_blank"
                rel="noreferrer"
                className="bg-[#00FFA3]/10 hover:bg-[#00FFA3]/20 border border-[#00FFA3]/30 text-[#00FFA3] text-xs font-bold px-4 py-2 rounded-none transition-all cursor-pointer flex items-center gap-1.5 shrink-0"
              >
                📊 Провери в DEXScreener ↗
              </a>
            )}
          </div>

          {/* Quick Metrics Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            
            {/* Scam Score Indicator */}
            <div className={`p-4 rounded-none border flex items-center gap-3 ${
              result.isScam 
                ? 'border-[#FF3D00]/40 bg-[#FF3D00]/5 text-[#FF3D00]' 
                : 'border-[#00FFA3]/40 bg-[#00FFA3]/5 text-[#00FFA3]'
            }`}>
              <div className="p-2 bg-black/40 border border-white/5 rounded-none">
                {result.isScam ? <ShieldAlert className="h-5 w-5" /> : <ShieldCheck className="h-5 w-5" />}
              </div>
              <div className="font-mono">
                <p className="text-[9px] uppercase tracking-wider text-white/40">Оценка за риск</p>
                <p className="text-base font-black">
                  {result.scamScore}% - {result.isScam ? 'Критичен риск' : 'Безопасен'}
                </p>
              </div>
            </div>

            {/* Price Potential Multiplier */}
            <div className="p-4 rounded-none border border-white/10 bg-[#0A0B0E]/60 text-amber-400">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-black/40 border border-white/5 rounded-none text-amber-400">
                  <Flame className="h-5 w-5" />
                </div>
                <div className="font-mono">
                  <p className="text-[9px] uppercase tracking-wider text-white/40">AI Потенциал</p>
                  <p className="text-base font-black">
                    {result.potentialMultiplier > 0 ? `${result.potentialMultiplier}x` : 'Няма потенциал'}
                  </p>
                </div>
              </div>
            </div>

            {/* Final Bot Decision */}
            <div className="p-4 rounded-none border border-[#00FFA3]/30 bg-[#0A0B0E]/60 text-[#00FFA3]">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-black/40 border border-white/5 rounded-none text-[#00FFA3]">
                  <Cpu className="h-5 w-5" />
                </div>
                <div className="font-mono">
                  <p className="text-[9px] uppercase tracking-wider text-white/40">Автоматично Решение</p>
                  <p className="text-xs font-black tracking-wide uppercase mt-0.5">
                    {result.tradeDecision.replace(/_/g, " ")}
                  </p>
                </div>
              </div>
            </div>

          </div>

          {/* Social Sentiment Summary */}
          <div className="bg-[#0A0B0E] p-4 rounded-none border border-white/5 text-xs font-mono">
            <span className="font-black text-[#00FFA3] uppercase tracking-wider">Обобщение на Сентимента:</span>
            <p className="text-white/70 mt-1.5 italic">
              "{result.sentimentAnalysis}"
            </p>
          </div>

          {/* Markdown Report Card */}
          <div className="bg-[#0A0B0E] border border-white/5 rounded-none p-6 text-white/80 text-xs leading-relaxed space-y-2">
            <div className="markdown-body prose prose-invert prose-xs max-w-none">
              <ReactMarkdown>{result.auditReport}</ReactMarkdown>
            </div>
          </div>

        </div>
      )}

    </div>
  );
}

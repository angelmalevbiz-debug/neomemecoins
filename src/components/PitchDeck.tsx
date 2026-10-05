import React from 'react';
import { ShieldCheck, Zap, AlertTriangle, Coins, TrendingUp, HelpCircle, Cpu, Eye } from 'lucide-react';

export default function PitchDeck() {
  return (
    <div id="pitch-deck" className="bg-white/[0.02] border border-white/10 rounded-none p-6 text-white/90 shadow-xl space-y-6">
      {/* Head section */}
      <div className="border-b border-white/10 pb-4">
        <span className="text-[10px] font-mono bg-white/10 text-[#00FFA3] px-2.5 py-1 rounded-none border border-white/20 uppercase tracking-widest font-black">
          Обосновка за Клиента / Client Value Deck
        </span>
        <h2 className="text-xl font-bold tracking-tight text-white mt-3">
          Защо този бот струва €1,000 (вместо масовите €70-80 ботове)?
        </h2>
        <p className="text-xs text-white/40 mt-1">
          Кратки, обективни и технически аргументи, обясняващи разликата в класа, скоростта и сигурността.
        </p>
      </div>

      {/* Comparison Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Standard €70 Bot Card */}
        <div className="bg-[#0A0B0E]/60 border border-[#FF3D00]/20 rounded-none p-5 space-y-3">
          <div className="flex items-center gap-2 text-[#FF3D00]">
            <AlertTriangle className="h-5 w-5" />
            <h3 className="font-bold text-xs uppercase tracking-wider font-mono">Масов Бот за €70 - €80</h3>
          </div>
          <ul className="space-y-2 text-xs text-white/60 list-disc list-inside leading-relaxed">
            <li><span className="text-white/90 font-semibold">Сляпо купуване (Simple Sniper):</span> Купува всеки нов коин без филтриране, което води до 90%+ загуби от скамове (Rugpulls).</li>
            <li><span className="text-white/90 font-semibold">Ръчна реакция:</span> Сигнализира, но изисква човекът да кликне ръчно или да чака бавна трансакция, губейки критични секунди.</li>
            <li><span className="text-white/90 font-semibold">Без анализ на портфейлите:</span> Не проверява кой е създателят и дали е лъгал инвеститори в минали сделки.</li>
            <li><span className="text-white/90 font-semibold">Без светкавичен Stop-Loss:</span> Големи загуби при спад, тъй като няма бърза реакция на блокчейн ниво.</li>
          </ul>
        </div>

        {/* Elite €1,000 Bot Card */}
        <div className="bg-[#0A0B0E]/80 border border-[#00FFA3]/30 rounded-none p-5 space-y-3 shadow-[0_0_20px_rgba(0,255,163,0.05)]">
          <div className="flex items-center gap-2 text-[#00FFA3]">
            <ShieldCheck className="h-5 w-5" />
            <h3 className="font-bold text-xs uppercase tracking-wider font-mono">Нашият Интелигентен AI Бот (€1,000)</h3>
          </div>
          <ul className="space-y-2 text-xs text-white/80 list-disc list-inside leading-relaxed">
            <li><span className="text-[#00FFA3] font-semibold">Двоен Анализ на Автопилот:</span> Twitter проучване на известни коинове + незабавно Copy Trading следене.</li>
            <li><span className="text-[#00FFA3] font-semibold">Проверка за Скамове:</span> Автоматичен одит на ликвидността (Liquidity Lock) и концентрацията на притежателите.</li>
            <li><span className="text-[#00FFA3] font-semibold">Пълен Милисекунден Контрол:</span> Връзка директно с Phantom за светкавичен вход и изход преди останалите.</li>
            <li><span className="text-[#00FFA3] font-semibold">Ултра-Строг Stop-Loss:</span> При най-малък признак на спад затваря за стотинки загуба.</li>
          </ul>
        </div>
      </div>

      {/* Structured Key Features */}
      <div className="space-y-4">
        <h4 className="text-[10px] font-mono text-white/40 uppercase tracking-widest border-b border-white/10 pb-1.5 font-bold">
          Ключови Технически Предимства на Робота
        </h4>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
          {/* Feature 1 */}
          <div className="flex gap-3 bg-[#0A0B0E]/40 p-4 rounded-none border border-white/5">
            <Cpu className="h-5 w-5 text-[#00FFA3] shrink-0 mt-0.5" />
            <div>
              <p className="font-bold text-white/90">1. Скорост от Десетки Проверки в Секунда</p>
              <p className="text-white/50 mt-1 leading-relaxed">Оваря, сканира и проверява десетки социални постове и смарт контракти едновременно. Човек би изгубил минути за един коин; ботът го прави за части от секундата.</p>
            </div>
          </div>

          {/* Feature 2 */}
          <div className="flex gap-3 bg-[#0A0B0E]/40 p-4 rounded-none border border-white/5">
            <Eye className="h-5 w-5 text-[#00FFA3] shrink-0 mt-0.5" />
            <div>
              <p className="font-bold text-white/90">2. Twitter DEX-Paid Радар & Ранен Вход</p>
              <p className="text-white/50 mt-1 leading-relaxed">Следи в реално време кои известни меме коинове активират платена реклама в DEXScreener (DEX Paid). Влиза веднага при старта и продава на първия скок преди останалите.</p>
            </div>
          </div>

          {/* Feature 3 */}
          <div className="flex gap-3 bg-[#0A0B0E]/40 p-4 rounded-none border border-white/5">
            <TrendingUp className="h-5 w-5 text-[#00FFA3] shrink-0 mt-0.5" />
            <div>
              <p className="font-bold text-white/90">3. Изпреварващ Elite Copy Trading</p>
              <p className="text-white/50 mt-1 leading-relaxed">Следи най-печелившите портфейли за седмицата. Купува моментално милисекунди след тяхното влизане и продава точно една стъпка преди те самите да излязат.</p>
            </div>
          </div>

          {/* Feature 4 */}
          <div className="flex gap-3 bg-[#0A0B0E]/40 p-4 rounded-none border border-white/5">
            <ShieldCheck className="h-5 w-5 text-[#00FFA3] shrink-0 mt-0.5" />
            <div>
              <p className="font-bold text-white/90">4. Загуби, Ограничени до Стотинки</p>
              <p className="text-white/50 mt-1 leading-relaxed">В днешния пазар 95% са скамове. Ботът има вграден авариен спирачен механизъм (Stop Loss), който продава автоматично при внезапен спад, свеждайки загубата до стотинки.</p>
            </div>
          </div>
        </div>
      </div>

      {/* Autopilot Notice Footer */}
      <div className="bg-[#00FFA3]/5 border border-[#00FFA3]/20 rounded-none p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs font-mono">
        <div className="space-y-1">
          <p className="font-bold text-[#00FFA3]">Напълно Автономен Срещу Сигнален Режим</p>
          <p className="text-white/60">Ботът предлага два избора: изпращане само на високоскоростни сигнали или изцяло автоматичен автопилот с демо/реални пари.</p>
        </div>
        <div className="bg-[#00FFA3]/10 text-[#00FFA3] px-3 py-1.5 rounded-none border border-[#00FFA3]/20 text-center uppercase tracking-wider shrink-0 font-black">
          Свързан с Phantom Wallet
        </div>
      </div>
    </div>
  );
}

import React from 'react';
import { Sliders, ShieldCheck, Bell, Coins, DollarSign } from 'lucide-react';
import { BotSettings } from '../types';
import GoogleDriveBackup from './GoogleDriveBackup';

interface SettingsProps {
  settings: BotSettings;
  setSettings: React.Dispatch<React.SetStateAction<BotSettings>>;
}

export default function SettingsPanel({ settings, setSettings }: SettingsProps) {
  
  const handleChange = (key: keyof BotSettings, value: any) => {
    setSettings(prev => ({
      ...prev,
      [key]: value
    }));
  };

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      
      {/* Intro visual card */}
      <div className="bg-gradient-to-r from-teal-950/20 via-[#0E101B]/40 to-emerald-950/20 border border-white/10 p-5 rounded-2xl">
        <h2 className="text-sm font-black font-mono text-[#00FFA3] uppercase tracking-wider flex items-center gap-1.5">
          <Sliders className="h-4 w-4" />
          НАСТРОЙКИ НА РИСК МЕНИДЖМЪНТА
        </h2>
        <p className="text-[11px] text-white/50 mt-1.5 leading-relaxed">
          Тези филтри управляват как роботът оперира и разпределя капитала ви. Стриктният контрол на риска е най-важната част от снайперирането – системата EX-1000 Premier спира загубите на милисекундата, за да защити вашите SOL средства.
        </p>
      </div>

      {/* DEFAULT SETTINGS FORM */}
      <div className="bg-[#0C0D15]/80 backdrop-blur-md border border-white/10 rounded-2xl p-6 shadow-xl space-y-6">
        
        {/* Title */}
        <div className="border-b border-white/5 pb-4">
          <h3 className="font-bold tracking-tight text-white text-sm font-mono uppercase">Конфигурация на Риска</h3>
          <p className="text-[11px] text-white/40 mt-1 leading-relaxed">
            Настройте точните параметри на бот симулацията или на реалния снайпер режим.
          </p>
        </div>

        {/* Settings inputs */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 text-xs">
          
          {/* Stop Loss sizing */}
          <div className="space-y-2 bg-white/[0.01] border border-white/5 p-4 rounded-xl">
            <label className="text-white/80 font-bold flex items-center gap-1.5 uppercase tracking-wider font-mono text-[10px]">
              <ShieldCheck className="h-4 w-4 text-red-400" />
              Стриктен Stop-Loss (%)
            </label>
            <div className="flex items-center gap-3 pt-1">
              <input 
                type="range" 
                min="0.5" 
                max="5.0" 
                step="0.1"
                value={settings.stopLossPercent}
                onChange={(e) => handleChange('stopLossPercent', parseFloat(e.target.value))}
                className="flex-1 accent-red-500 cursor-pointer"
              />
              <span className="font-mono font-bold bg-black/40 px-2.5 py-1.5 border border-white/5 rounded-lg text-red-400 shrink-0">
                -{settings.stopLossPercent}%
              </span>
            </div>
            <p className="text-[10px] text-white/40 leading-relaxed font-mono pt-1">
              Бърз изход при падане. Загубата се ограничава до стотинки (напр. 0.005 SOL), предотвратявайки пълно нулиране.
            </p>
          </div>

          {/* Take Profit multiplier */}
          <div className="space-y-2 bg-white/[0.01] border border-white/5 p-4 rounded-xl">
            <label className="text-white/80 font-bold flex items-center gap-1.5 uppercase tracking-wider font-mono text-[10px]">
              <Coins className="h-4 w-4 text-[#00FFA3]" />
              Бърз Take-Profit / Изход при Ръст (%)
            </label>
            <div className="flex items-center gap-3 pt-1">
              <input 
                type="range" 
                min="5" 
                max="150" 
                step="5"
                value={settings.takeProfitPercent}
                onChange={(e) => handleChange('takeProfitPercent', parseFloat(e.target.value))}
                className="flex-1 accent-[#00FFA3] cursor-pointer"
              />
              <span className="font-mono font-bold bg-black/40 px-2.5 py-1.5 border border-white/5 rounded-lg text-[#00FFA3] shrink-0">
                +{settings.takeProfitPercent}%
              </span>
            </div>
            <p className="text-[10px] text-white/40 leading-relaxed font-mono pt-1">
              Автоматично затваряне и прибиране на печалбата веднага след влизане на китове и изстрелване на цената нагоре.
            </p>
          </div>

          {/* Trade Size */}
          <div className="space-y-2 bg-white/[0.01] border border-white/5 p-4 rounded-xl">
            <label className="text-white/80 font-bold flex items-center gap-1.5 uppercase tracking-wider font-mono text-[10px]">
              <DollarSign className="h-4 w-4 text-[#00FFA3]" />
              Размер на Единична Сделка (SOL)
            </label>
            <div className="relative pt-1">
              <input 
                type="number" 
                min="0.1" 
                max="5" 
                step="0.1"
                value={settings.tradeSizeSol}
                onChange={(e) => handleChange('tradeSizeSol', parseFloat(e.target.value))}
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2.5 text-white font-mono text-xs focus:outline-none focus:border-[#00FFA3]/40"
              />
              <span className="absolute right-3 top-3.5 text-white/30 font-mono text-[10px]">SOL</span>
            </div>
            <p className="text-[10px] text-white/40 leading-relaxed font-mono">
              Количеството SOL, което ботът ще инвестира автоматично във всеки одобрен меме коин при откриване на сигнал.
            </p>
          </div>

          {/* Alert integrations */}
          <div className="space-y-2 bg-white/[0.01] border border-white/5 p-4 rounded-xl">
            <label className="text-white/80 font-bold flex items-center gap-1.5 uppercase tracking-wider font-mono text-[10px]">
              <Bell className="h-4 w-4 text-purple-400" />
              Уведомления за Сигнали
            </label>
            <div className="flex items-center justify-between bg-black/40 p-2.5 rounded-xl border border-white/5 mt-1">
              <span className="text-white/40 font-mono text-[10px]">Telegram / Discord Webhooks</span>
              <button 
                onClick={() => handleChange('telegramEnabled', !settings.telegramEnabled)}
                className={`font-black px-4 py-1.5 rounded-lg border text-[9px] font-mono transition-all cursor-pointer ${
                  settings.telegramEnabled 
                    ? 'bg-[#00FFA3]/10 border-[#00FFA3]/30 text-[#00FFA3] shadow-[0_0_10px_rgba(0,255,163,0.15)]' 
                    : 'bg-white/5 border-white/10 text-white/30'
                }`}
              >
                {settings.telegramEnabled ? 'АКТИВНИ' : 'СПРЕНИ'}
              </button>
            </div>
            <p className="text-[10px] text-white/40 leading-relaxed font-mono">
              Изпраща незабавни криптирани известия със статистиката и Solscan връзките директно към мобилния ви телефон.
            </p>
          </div>

        </div>

      </div>

      {/* GOOGLE DRIVE BACKUP PANEL */}
      <GoogleDriveBackup />

    </div>
  );
}

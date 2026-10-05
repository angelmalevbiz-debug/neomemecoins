import { FormEvent, Fragment, ReactNode, useEffect, useMemo, useState } from 'react';
import { AlertCircle, CheckCircle2, LockKeyhole, LogIn, LogOut, ShieldCheck, UserPlus } from 'lucide-react';
import type { User } from '@supabase/supabase-js';
import { clearAccountStateCache } from '../lib/paperDashboardState';
import {
  getAuthRedirectUrl,
  isSupabaseConfigured,
  type NeoActiveSession,
  supabase,
} from '../lib/supabase';

type Mode = 'login' | 'signup';

function fallbackUsername(user: User) {
  const metadataUsername = String(user.user_metadata?.username || '').trim();
  if (metadataUsername) return metadataUsername;
  const emailPrefix = (user.email || '').split('@')[0]?.trim();
  return emailPrefix || 'NEO User';
}

function authMessage(message: string) {
  const value = message.toLowerCase();

  if (value.includes('invalid login credentials')) {
    return 'Невалиден имейл или парола.';
  }

  if (value.includes('user already registered')) {
    return 'Вече има регистрация с този имейл.';
  }

  if (value.includes('password should be at least')) {
    return 'Паролата трябва да е поне 6 символа.';
  }

  if (value.includes('email rate limit')) {
    return 'Изпратени са твърде много имейли. Опитай отново след малко.';
  }

  return message;
}

export default function SaaSAuthGate({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<Mode>('login');
  const [authUser, setAuthUser] = useState<User | null>(null);
  const [activeSession, setActiveSession] = useState<NeoActiveSession | null>(null);
  const [ready, setReady] = useState(false);
  const [profileLoading, setProfileLoading] = useState(false);

  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const [busy, setBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [successMessage, setSuccessMessage] = useState('');

  useEffect(() => {
    if (!supabase) {
      setReady(true);
      return;
    }

    let mounted = true;

    void supabase.auth.getSession().then(({ data }) => {
      if (!mounted) return;
      if (!data.session) clearAccountStateCache(sessionStorage);
      setAuthUser(data.session?.user || null);
      setReady(true);
    });

    const { data: listener } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!mounted) return;
      if (!session) clearAccountStateCache(sessionStorage);
      setAuthUser(session?.user || null);
      setReady(true);
    });

    return () => {
      mounted = false;
      listener.subscription.unsubscribe();
    };
  }, []);

  useEffect(() => {
    if (!authUser || !supabase) {
      setActiveSession(null);
      sessionStorage.removeItem('saas_active_session');
      if (ready) clearAccountStateCache(sessionStorage);
      return;
    }

    let cancelled = false;
    setProfileLoading(true);

    const loadProfile = async () => {
      const { data } = await supabase
        .from('profiles')
        .select('id, username, plan, created_at')
        .eq('id', authUser.id)
        .maybeSingle();

      if (cancelled) return;

      const nextSession: NeoActiveSession = {
        userId: authUser.id,
        email: authUser.email || '',
        username: data?.username || fallbackUsername(authUser),
        plan: data?.plan || 'demo',
        createdAt: data?.created_at || authUser.created_at,
      };

      sessionStorage.setItem('saas_active_session', JSON.stringify(nextSession));
      setActiveSession(nextSession);
      setProfileLoading(false);
    };

    void loadProfile();

    return () => {
      cancelled = true;
    };
  }, [authUser?.id, ready]);

  useEffect(() => {
    setErrorMessage('');
    setSuccessMessage('');
  }, [mode]);

  const canSubmit = useMemo(() => {
    if (!email.trim() || !password) return false;
    if (mode === 'signup' && (!username.trim() || !confirmPassword)) return false;
    return true;
  }, [mode, username, email, password, confirmPassword]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setErrorMessage('');
    setSuccessMessage('');

    if (!supabase || !isSupabaseConfigured) {
      setErrorMessage('Supabase publishable key не е конфигуриран.');
      return;
    }

    const cleanEmail = email.trim().toLowerCase();

    if (!cleanEmail || !password) {
      setErrorMessage('Попълни имейл и парола.');
      return;
    }

    if (mode === 'signup') {
      const cleanUsername = username.trim();

      if (cleanUsername.length < 2) {
        setErrorMessage('Името трябва да е поне 2 символа.');
        return;
      }

      if (password.length < 6) {
        setErrorMessage('Паролата трябва да е поне 6 символа.');
        return;
      }

      if (password !== confirmPassword) {
        setErrorMessage('Паролите не съвпадат.');
        return;
      }

      setBusy(true);

      const { data, error } = await supabase.auth.signUp({
        email: cleanEmail,
        password,
        options: {
          emailRedirectTo: getAuthRedirectUrl(),
          data: {
            username: cleanUsername,
          },
        },
      });

      setBusy(false);

      if (error) {
        setErrorMessage(authMessage(error.message));
        return;
      }

      if (data.session) {
        setSuccessMessage('Регистрацията е готова. Влизаме в NEO...');
      } else {
        setSuccessMessage('Регистрацията е създадена. Провери имейла си и потвърди акаунта.');
      }

      return;
    }

    setBusy(true);
    const { error } = await supabase.auth.signInWithPassword({
      email: cleanEmail,
      password,
    });
    setBusy(false);

    if (error) {
      setErrorMessage(authMessage(error.message));
      return;
    }

    setSuccessMessage('Успешен вход. Зареждам NEO...');
  };

  const handleLogout = async () => {
    if (!supabase) return;
    setBusy(true);
    const { error } = await supabase.auth.signOut();
    clearAccountStateCache(sessionStorage);
    if (error) {
      setErrorMessage(authMessage(error.message));
      setBusy(false);
      return;
    }
    sessionStorage.removeItem('saas_active_session');
    setActiveSession(null);
    setBusy(false);
  };

  if (!ready || (authUser && (profileLoading || activeSession?.userId !== authUser.id))) {
    return (
      <div className="min-h-screen bg-[#08090c] text-white flex items-center justify-center">
        <div className="flex items-center gap-3 text-sm text-white/60">
          <span className="h-5 w-5 rounded-full border-2 border-emerald-400 border-t-transparent animate-spin" />
          Зареждане на NEO...
        </div>
      </div>
    );
  }

  if (authUser && activeSession) {
    return (
      <>
        <div className="fixed right-4 top-4 z-[100] flex items-center gap-2 rounded-xl border border-white/10 bg-[#0b0c10]/90 px-3 py-2 shadow-2xl backdrop-blur-xl">
          <div className="min-w-0 text-right">
            <div className="max-w-[180px] truncate text-[11px] font-bold text-white">
              {activeSession.username}
            </div>
            <div className="text-[9px] uppercase tracking-[0.14em] text-emerald-300">
              {activeSession.plan}
            </div>
          </div>
          <button
            type="button"
            onClick={handleLogout}
            disabled={busy}
            title="Изход"
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 bg-white/[0.04] text-white/60 transition hover:border-red-400/30 hover:bg-red-400/10 hover:text-red-300 disabled:opacity-50"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
        <Fragment key={authUser.id}>{children}</Fragment>
      </>
    );
  }

  return (
    <div className="min-h-screen bg-[#08090c] text-white flex items-center justify-center px-4 py-10">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_50%_0%,rgba(16,185,129,0.10),transparent_38%)]" />

      <div className="relative w-full max-w-md rounded-[28px] border border-white/[0.08] bg-[#0d0f13]/95 p-6 shadow-2xl backdrop-blur-xl sm:p-8">
        <div className="mb-7 flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-emerald-400/20 bg-emerald-400/10">
            <ShieldCheck className="h-6 w-6 text-emerald-300" />
          </div>
          <div>
            <div className="text-[10px] font-black uppercase tracking-[0.22em] text-emerald-300">
              NEO MEME COINS
            </div>
            <h1 className="mt-1 text-xl font-black tracking-tight text-white">
              {mode === 'login' ? 'Вход в NEO' : 'Създай тестов акаунт'}
            </h1>
            <p className="mt-1 text-xs leading-5 text-white/45">
              Реален Supabase акаунт. Паролата не се пази в браузъра.
            </p>
          </div>
        </div>

        {!isSupabaseConfigured && (
          <div className="mb-5 flex gap-2 rounded-xl border border-amber-400/20 bg-amber-400/10 p-3 text-xs text-amber-200">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              Липсва VITE_SUPABASE_PUBLISHABLE_KEY. Добави publishable key от Supabase Project Settings → API.
            </span>
          </div>
        )}

        <div className="mb-6 grid grid-cols-2 rounded-xl border border-white/[0.07] bg-black/30 p-1">
          <button
            type="button"
            onClick={() => setMode('login')}
            className={
              'rounded-lg px-3 py-2.5 text-xs font-bold transition ' +
              (mode === 'login'
                ? 'bg-white/[0.07] text-white'
                : 'text-white/40 hover:text-white/70')
            }
          >
            Вход
          </button>
          <button
            type="button"
            onClick={() => setMode('signup')}
            className={
              'rounded-lg px-3 py-2.5 text-xs font-bold transition ' +
              (mode === 'signup'
                ? 'bg-white/[0.07] text-white'
                : 'text-white/40 hover:text-white/70')
            }
          >
            Регистрация
          </button>
        </div>

        {errorMessage && (
          <div className="mb-4 flex gap-2 rounded-xl border border-red-400/20 bg-red-400/10 p-3 text-xs text-red-200">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{errorMessage}</span>
          </div>
        )}

        {successMessage && (
          <div className="mb-4 flex gap-2 rounded-xl border border-emerald-400/20 bg-emerald-400/10 p-3 text-xs text-emerald-200">
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{successMessage}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {mode === 'signup' && (
            <label className="block">
              <span className="mb-1.5 block text-[10px] font-black uppercase tracking-[0.14em] text-white/40">
                Име
              </span>
              <input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="nickname"
                placeholder="Напр. Angel"
                className="w-full rounded-xl border border-white/10 bg-black/30 px-4 py-3 text-sm text-white outline-none transition placeholder:text-white/20 focus:border-emerald-400/40"
              />
            </label>
          )}

          <label className="block">
            <span className="mb-1.5 block text-[10px] font-black uppercase tracking-[0.14em] text-white/40">
              Имейл
            </span>
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              placeholder="you@example.com"
              className="w-full rounded-xl border border-white/10 bg-black/30 px-4 py-3 text-sm text-white outline-none transition placeholder:text-white/20 focus:border-emerald-400/40"
            />
          </label>

          <label className="block">
            <span className="mb-1.5 block text-[10px] font-black uppercase tracking-[0.14em] text-white/40">
              Парола
            </span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              placeholder="Минимум 6 символа"
              className="w-full rounded-xl border border-white/10 bg-black/30 px-4 py-3 text-sm text-white outline-none transition placeholder:text-white/20 focus:border-emerald-400/40"
            />
          </label>

          {mode === 'signup' && (
            <label className="block">
              <span className="mb-1.5 block text-[10px] font-black uppercase tracking-[0.14em] text-white/40">
                Повтори паролата
              </span>
              <input
                type="password"
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
                autoComplete="new-password"
                placeholder="Повтори паролата"
                className="w-full rounded-xl border border-white/10 bg-black/30 px-4 py-3 text-sm text-white outline-none transition placeholder:text-white/20 focus:border-emerald-400/40"
              />
            </label>
          )}

          <button
            type="submit"
            disabled={busy || !canSubmit || !isSupabaseConfigured}
            className="mt-2 flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-400 px-4 py-3.5 text-sm font-black text-[#06110d] transition hover:bg-emerald-300 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? (
              <span className="h-4 w-4 rounded-full border-2 border-[#06110d] border-t-transparent animate-spin" />
            ) : mode === 'login' ? (
              <LogIn className="h-4 w-4" />
            ) : (
              <UserPlus className="h-4 w-4" />
            )}
            {mode === 'login' ? 'Влез в NEO' : 'Създай акаунт'}
          </button>
        </form>

        <div className="mt-6 flex items-start gap-2 border-t border-white/[0.06] pt-5 text-[10px] leading-4 text-white/30">
          <LockKeyhole className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-300/70" />
          <span>
            Това е тестова среда. Регистрацията дава достъп до paper-trading версията и не активира реална търговия с пари.
          </span>
        </div>
      </div>
    </div>
  );
}

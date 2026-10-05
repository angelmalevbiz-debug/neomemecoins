import { createClient } from '@supabase/supabase-js';

const defaultProjectUrl = 'https://qziuovwcauaklgqscqys.supabase.co';
const defaultPublishableKey = 'sb_publishable_IVNFzAWi1kb7NjwTBitA_g_gBVnvrie';

const supabaseUrl =
  (import.meta as any).env.VITE_SUPABASE_URL?.trim() || defaultProjectUrl;

const supabasePublishableKey =
  (import.meta as any).env.VITE_SUPABASE_PUBLISHABLE_KEY?.trim() ||
  (import.meta as any).env.VITE_SUPABASE_ANON_KEY?.trim() ||
  defaultPublishableKey;

export const isSupabaseConfigured = Boolean(supabaseUrl && supabasePublishableKey);

export const supabase = isSupabaseConfigured
  ? createClient(supabaseUrl, supabasePublishableKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    })
  : null;

export type NeoProfile = {
  id: string;
  username: string;
  plan: string;
  created_at: string;
};

export type NeoActiveSession = {
  userId: string;
  email: string;
  username: string;
  plan: string;
  createdAt: string;
};

export function getAuthRedirectUrl() {
  return new URL('.', window.location.href).toString();
}

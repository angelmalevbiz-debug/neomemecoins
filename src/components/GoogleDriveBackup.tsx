import React, { useState, useEffect } from 'react';
import { 
  Cloud, 
  FolderArchive, 
  Download, 
  ExternalLink, 
  CheckCircle2, 
  AlertCircle, 
  LogOut, 
  Loader2, 
  User as UserIcon,
  ShieldCheck
} from 'lucide-react';
import { User } from 'firebase/auth';
import { 
  initGoogleAuth, 
  googleSignIn, 
  googleSignOut, 
  getAccessToken 
} from '../lib/googleAuth';

export default function GoogleDriveBackup() {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [needsAuth, setNeedsAuth] = useState(true);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [uploadResult, setUploadResult] = useState<{
    fileId: string;
    fileName: string;
    viewLink: string;
  } | null>(null);

  useEffect(() => {
    // Listen for authentication changes and pick up cached tokens
    const unsubscribe = initGoogleAuth(
      (currentUser, cachedToken) => {
        setUser(currentUser);
        setToken(cachedToken);
        setNeedsAuth(false);
      },
      () => {
        setUser(null);
        setToken(null);
        setNeedsAuth(true);
      }
    );
    return () => unsubscribe();
  }, []);

  const handleLogin = async () => {
    setIsLoggingIn(true);
    setErrorMsg(null);
    try {
      const result = await googleSignIn();
      if (result) {
        setUser(result.user);
        setToken(result.accessToken);
        setNeedsAuth(false);
      }
    } catch (err: any) {
      console.error('Login failed:', err);
      setErrorMsg(err.message || 'Грешка при опит за влизане с Google.');
    } finally {
      setIsLoggingIn(false);
    }
  };

  const handleLogout = async () => {
    try {
      await googleSignOut();
      setUser(null);
      setToken(null);
      setNeedsAuth(true);
      setUploadResult(null);
      setErrorMsg(null);
    } catch (err: any) {
      console.error('Logout failed:', err);
    }
  };

  const handleBackupToDrive = async () => {
    const confirmed = window.confirm(
      "Потвърждавате ли архивирането на текущия код на проекта и качването му във вашия Google Drive профил?"
    );
    if (!confirmed) return;

    setIsUploading(true);
    setErrorMsg(null);
    setUploadResult(null);

    try {
      const currentToken = token || (await getAccessToken());
      if (!currentToken) {
        setNeedsAuth(true);
        throw new Error("Сесията е изтекълa. Моля, влезте отново.");
      }

      const res = await fetch('/api/upload-to-drive', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${currentToken}`,
          'Content-Type': 'application/json'
        }
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || "Неуспешно качване на архива.");
      }

      setUploadResult({
        fileId: data.fileId,
        fileName: data.fileName,
        viewLink: data.viewLink
      });
    } catch (err: any) {
      console.error('Backup failed:', err);
      setErrorMsg(err.message || "Грешка при създаването или качването на архива.");
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div id="google-drive-backup-panel" className="bg-[#0C0D15]/80 backdrop-blur-md border border-purple-500/20 rounded-2xl p-6 shadow-xl space-y-6">
      
      {/* Title block */}
      <div className="flex flex-col md:flex-row md:items-center justify-between border-b border-white/5 pb-4 gap-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Cloud className="h-5 w-5 text-purple-400" />
            <h3 className="text-base font-black tracking-wider uppercase font-mono text-white">
              Google Drive Архивиране на Проекта
            </h3>
          </div>
          <p className="text-xs text-white/50">
            Свалете пълния сорс код на вашия Solana търговски бот директно на вашия Google Drive като ZIP архив.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        
        {/* Left info description column */}
        <div className="lg:col-span-6 space-y-4">
          <div className="bg-purple-950/10 border border-purple-500/15 p-4 rounded-xl space-y-3">
            <h4 className="text-xs font-bold text-purple-300 uppercase tracking-widest font-mono flex items-center gap-1.5">
              <ShieldCheck className="h-4 w-4 text-[#00FFA3]" />
              Сигурно и Изчистено Архивиране
            </h4>
            <p className="text-[11px] text-white/70 leading-relaxed font-sans">
              Тази система компресира целия ви персонален проект (включително вашите React компоненти, TypeScript конфиг, Express сървър и настройки на симулатора).
            </p>
            <ul className="text-[10px] text-white/50 space-y-1 font-mono list-disc list-inside">
              <li>Автоматично изключва тежките <code className="text-purple-300">node_modules</code>.</li>
              <li>Изключва компилираните папки за максимална бързина.</li>
              <li>Качва архива сигурно и директно към вашия личен Google Drive.</li>
            </ul>
          </div>

          {errorMsg && (
            <div className="flex items-start gap-2 bg-red-950/20 border border-red-500/20 p-3.5 rounded-xl font-mono text-[11px] text-red-400">
              <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
              <div>
                <strong className="font-bold uppercase tracking-wider block mb-1">Грешка при операция:</strong>
                {errorMsg}
              </div>
            </div>
          )}
        </div>

        {/* Right Authentication & Action control column */}
        <div className="lg:col-span-6 flex flex-col justify-center">
          
          {needsAuth ? (
            <div className="bg-white/[0.01] border border-white/5 rounded-xl p-6 text-center space-y-5">
              <div className="space-y-1.5">
                <span className="text-[10px] text-white/30 font-mono uppercase tracking-widest block">Google Двуфакторна Ототоризация</span>
                <p className="text-xs text-white/70">Моля, влезте с вашия Google акаунт, за да предоставите права за запазване на файлове в Google Drive.</p>
              </div>

              <div className="flex justify-center">
                {/* Official styled Sign-in with Google button */}
                <button 
                  onClick={handleLogin}
                  disabled={isLoggingIn}
                  className="gsi-material-button group relative flex items-center justify-center gap-3 bg-white hover:bg-gray-100 text-gray-900 font-bold px-5 py-3 rounded-xl transition-all shadow-[0_4px_20px_rgba(255,255,255,0.05)] cursor-pointer overflow-hidden disabled:opacity-50"
                >
                  <div className="gsi-material-button-icon h-5 w-5 shrink-0">
                    <svg version="1.1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" style={{ display: "block" }}>
                      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"></path>
                      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"></path>
                      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"></path>
                      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"></path>
                      <path fill="none" d="M0 0h48v48H0z"></path>
                    </svg>
                  </div>
                  <span className="text-xs font-sans tracking-wide">Влез с Google профил</span>
                  {isLoggingIn && (
                    <div className="absolute inset-0 bg-white/80 flex items-center justify-center">
                      <Loader2 className="h-5 w-5 text-purple-600 animate-spin" />
                    </div>
                  )}
                </button>
              </div>
            </div>
          ) : (
            <div className="bg-white/[0.01] border border-white/5 rounded-xl p-5 space-y-4">
              
              {/* Connected User Header */}
              <div className="flex items-center justify-between border-b border-white/5 pb-3">
                <div className="flex items-center gap-2.5">
                  {user?.photoURL ? (
                    <img 
                      src={user.photoURL} 
                      alt="Avatar" 
                      className="h-8 w-8 rounded-full border border-purple-500/30"
                      referrerPolicy="no-referrer"
                    />
                  ) : (
                    <div className="h-8 w-8 rounded-full bg-purple-950/60 border border-purple-500/30 flex items-center justify-center">
                      <UserIcon className="h-4 w-4 text-purple-300" />
                    </div>
                  )}
                  <div>
                    <span className="text-xs font-bold text-white block leading-tight">{user?.displayName || 'Google Потребител'}</span>
                    <span className="text-[9px] text-white/40 block leading-none font-mono mt-0.5">{user?.email}</span>
                  </div>
                </div>

                <button 
                  onClick={handleLogout}
                  title="Излизане"
                  className="p-1.5 hover:bg-white/5 rounded-lg border border-white/5 text-white/40 hover:text-white transition-all cursor-pointer"
                >
                  <LogOut className="h-3.5 w-3.5" />
                </button>
              </div>

              {/* Action Trigger Card */}
              {!uploadResult ? (
                <button
                  onClick={handleBackupToDrive}
                  disabled={isUploading}
                  className="w-full py-3 bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 disabled:from-purple-950 disabled:to-indigo-950 text-white font-black uppercase text-[10px] tracking-widest rounded-xl transition-all shadow-[0_4px_12px_rgba(147,51,234,0.3)] flex items-center justify-center gap-2 cursor-pointer"
                >
                  {isUploading ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      <span>АРХИВИРАНЕ И КАЧВАНЕ...</span>
                    </>
                  ) : (
                    <>
                      <FolderArchive className="h-4 w-4 text-[#00FFA3]" />
                      <span>АРХИВИРАЙ И КАЧИ В GOOGLE DRIVE</span>
                    </>
                  )}
                </button>
              ) : (
                <div className="space-y-4">
                  {/* Success notification */}
                  <div className="bg-emerald-950/20 border border-emerald-500/20 p-3 rounded-lg flex items-start gap-2 text-[10px] font-mono text-emerald-400">
                    <CheckCircle2 className="h-4 w-4 shrink-0 mt-0.5" />
                    <div>
                      <strong className="font-bold uppercase block mb-0.5">УСПЕШНО КАЧВАНЕ!</strong>
                      Сорс кодът на проекта беше пакетиран и записан на сигурно място във вашия Google Drive.
                    </div>
                  </div>

                  {/* Dynamic Download details */}
                  <div className="bg-black/40 border border-white/5 p-3.5 rounded-xl space-y-1.5 text-[10px] font-mono">
                    <div className="flex justify-between">
                      <span className="text-white/40">Файл:</span>
                      <strong className="text-white truncate max-w-[200px]">{uploadResult.fileName}</strong>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-white/40">Drive ID:</span>
                      <span className="text-purple-300 select-all truncate max-w-[200px]">{uploadResult.fileId}</span>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    {/* View/Download Link */}
                    <a 
                      href={uploadResult.viewLink}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="py-2.5 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-black uppercase text-[9px] tracking-widest rounded-lg transition-all text-center flex items-center justify-center gap-1.5 shadow-[0_4px_12px_rgba(16,185,129,0.2)]"
                    >
                      <Download className="h-3.5 w-3.5" />
                      Свали файл
                    </a>

                    <button
                      onClick={() => setUploadResult(null)}
                      className="py-2.5 bg-white/5 hover:bg-white/10 border border-white/5 text-white font-black uppercase text-[9px] tracking-widest rounded-lg transition-all text-center cursor-pointer"
                    >
                      Ново качване
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

        </div>
      </div>

    </div>
  );
}

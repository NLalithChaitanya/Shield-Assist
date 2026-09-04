/**
 * LoginPage — premium fintech login with split layout.
 * Mobile: tall gradient hero + floating form card.
 * Desktop: rich brand left panel + clean form right.
 */
import { useState, useEffect } from 'react';
import { Shield, Eye, EyeOff, Zap, FileSearch, Scale, CheckCircle2 } from 'lucide-react';

interface LoginPageProps { onLogin: () => void; }

export default function LoginPage({ onLogin }: LoginPageProps) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [mounted, setMounted] = useState(false);
  useEffect(() => { const t = setTimeout(() => setMounted(true), 80); return () => clearTimeout(t); }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault(); setLoading(true); setError('');
    await new Promise(r => setTimeout(r, 800));
    if (!email || !password) { setError('Please enter your email and password.'); setLoading(false); return; }
    onLogin();
  };

  const show = mounted;

  return (
    <div className="min-h-screen flex bg-surface">
      {/* ===== DESKTOP LEFT PANEL ===== */}
      <div className="hidden lg:flex lg:w-[460px] xl:w-[520px] flex-col relative overflow-hidden" style={{background:'linear-gradient(165deg, #060d1a 0%, #0c1e3d 35%, #13396a 70%, #1a4d8f 100%)'}}>
        {/* Glowing orbs */}
        <div className="absolute top-[-140px] right-[-100px] w-[400px] h-[400px] rounded-full" style={{background:'radial-gradient(circle, rgba(31,111,235,0.35) 0%, transparent 65%)'}} />
        <div className="absolute bottom-[-120px] left-[-70px] w-[300px] h-[300px] rounded-full" style={{background:'radial-gradient(circle, rgba(79,140,255,0.25) 0%, transparent 65%)'}} />
        {/* Dot grid */}
        <div className="absolute inset-0 opacity-[0.04]" style={{backgroundImage:'radial-gradient(circle, #fff 1px, transparent 1px)',backgroundSize:'32px 32px'}} />

        <div className="relative z-10 flex flex-col justify-between h-full p-10 xl:p-14">
          {/* Logo */}
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{background:'rgba(255,255,255,0.1)',backdropFilter:'blur(12px)',border:'1px solid rgba(255,255,255,0.08)'}}>
              <Shield size={20} className="text-white" />
            </div>
            <div>
              <div className="text-[15px] font-semibold text-white tracking-tight">Shield Assist</div>
              <div className="text-[10px] text-blue-200/50 uppercase tracking-[0.15em]">Dispute Operations</div>
            </div>
          </div>

          {/* Headline + features */}
          <div className={`transition-all duration-[800ms] ease-out ${show ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-5'}`}>
            <h1 className="text-[34px] xl:text-[38px] font-bold text-white leading-[1.12] tracking-tight mb-10">
              Resolve disputes<br />with{' '}
              <span className="bg-clip-text text-transparent" style={{backgroundImage:'linear-gradient(135deg, #7ec8f8, #c0e0ff)'}}>
                evidence.
              </span>
            </h1>
            <div className="space-y-4">
              <FeatureRow icon={<FileSearch size={15}/>} title="Every claim, sourced" desc="AI responses traceable to your documents. Nothing fabricated." />
              <FeatureRow icon={<Scale size={15}/>} title="Deterministic guardrails" desc="A rules-based gate blocks weak submissions — always." />
              <FeatureRow icon={<Zap size={15}/>} title="Minutes, not days" desc="From evidence upload to Razorpay-ready contest." />
            </div>
          </div>

          {/* Stats + compliance */}
          <div className={`transition-all duration-[800ms] delay-200 ease-out ${show ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
            <div className="flex gap-8 mb-6">
              <StatBlock value="81%" label="Win rate" />
              <StatBlock value="<5m" label="To contest" />
              <StatBlock value="0" label="AI fabrications" />
            </div>
            <div className="flex items-center gap-2 pt-5 border-t border-white/[0.08]">
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-400/70" />
              <span className="text-[10px] text-white/25 tracking-wide">SOC 2 · E2E Encrypted · Evidence Integrity Mode</span>
            </div>
          </div>
        </div>
      </div>
      {/* ===== RIGHT PANEL ===== */}
      <div className="flex-1 flex flex-col relative">
        {/* Mobile gradient hero */}
        <div className="lg:hidden relative" style={{background:'linear-gradient(165deg, #060d1a 0%, #0c1e3d 40%, #13396a 80%, #1a4d8f 100%)',paddingTop:'env(safe-area-inset, 20px)',paddingBottom:80,paddingLeft:28,paddingRight:28}}>
          <div className="absolute top-[-60px] right-[-30px] w-[220px] h-[220px] rounded-full" style={{background:'radial-gradient(circle, rgba(31,111,235,0.45) 0%, transparent 65%)'}} />
          <div className="absolute bottom-[-40px] left-[-20px] w-[150px] h-[150px] rounded-full" style={{background:'radial-gradient(circle, rgba(79,140,255,0.3) 0%, transparent 65%)'}} />
          <div className="absolute inset-0 opacity-[0.03]" style={{backgroundImage:'radial-gradient(circle, #fff 1px, transparent 1px)',backgroundSize:'28px 28px'}} />
          <div className="relative z-10 pt-6">
            {/* Logo */}
            <div className="flex items-center gap-3 mb-8">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{background:'rgba(255,255,255,0.1)',backdropFilter:'blur(12px)',border:'1px solid rgba(255,255,255,0.08)'}}>
                <Shield size={20} className="text-white" />
              </div>
              <div>
                <div className="text-[14px] font-semibold text-white tracking-tight">Shield Assist</div>
                <div className="text-[9px] text-blue-200/50 uppercase tracking-[0.15em]">Dispute Operations</div>
              </div>
            </div>
            {/* Hero headline */}
            <h1 className={`text-[28px] font-bold text-white leading-[1.15] tracking-tight transition-all duration-700 delay-100 ${show ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`}>
              Resolve disputes with{' '}
              <span className="bg-clip-text text-transparent" style={{backgroundImage:'linear-gradient(135deg, #7ec8f8, #c0e0ff)'}}>evidence.</span>
            </h1>
          </div>
        </div>

        {/* Form area */}
        <div className="flex-1 flex items-start lg:items-center justify-center px-5 lg:px-0 -mt-16 lg:mt-0 pb-12 lg:pb-0">
          <div className={`w-full max-w-[400px] transition-all duration-600 delay-150 ${show ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-6'}`}>
            {/* Desktop welcome (above card) */}
            <div className="hidden lg:block mb-6">
              <h2 className="text-[22px] font-bold text-ink tracking-tight">Welcome back</h2>
              <p className="text-[13px] text-ink-muted mt-1.5">Sign in to your dispute operations dashboard.</p>
            </div>

            {/* Elevated form card */}
            <div className="bg-surface-raised rounded-2xl p-7 lg:p-8 relative" style={{boxShadow:'0 1px 2px rgba(0,0,0,0.03), 0 4px 12px rgba(0,0,0,0.04), 0 16px 40px rgba(0,0,0,0.06)',border:'1px solid rgba(0,0,0,0.04)'}}>
              <form onSubmit={handleSubmit} className="space-y-5">
                {/* Email */}
                <div>
                  <label htmlFor="email" className="block text-[11px] font-semibold text-ink-muted mb-2 uppercase tracking-widest">Email address</label>
                  <input id="email" type="email" value={email} onChange={e => setEmail(e.target.value)}
                    className="w-full px-4 py-3 text-[13.5px] bg-surface-sunken border border-line rounded-xl text-ink placeholder-ink-faint focus:border-signal focus:ring-2 focus:ring-signal/10 outline-none transition-all duration-200"
                    placeholder="merchant@company.com" autoComplete="email" autoFocus />
                </div>

                {/* Password */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <label htmlFor="password" className="text-[11px] font-semibold text-ink-muted uppercase tracking-widest">Password</label>
                    <button type="button" className="text-[11px] text-signal hover:text-signal/80 font-medium transition-colors">Forgot?</button>
                  </div>
                  <div className="relative">
                    <input id="password" type={showPassword ? 'text' : 'password'} value={password} onChange={e => setPassword(e.target.value)}
                      className="w-full px-4 py-3 pr-11 text-[13.5px] bg-surface-sunken border border-line rounded-xl text-ink placeholder-ink-faint focus:border-signal focus:ring-2 focus:ring-signal/10 outline-none transition-all duration-200"
                      placeholder="Enter your password" autoComplete="current-password" />
                    <button type="button" onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-3.5 top-1/2 -translate-y-1/2 text-ink-faint hover:text-ink transition-colors"
                      aria-label={showPassword ? 'Hide password' : 'Show password'}>
                      {showPassword ? <EyeOff size={15}/> : <Eye size={15}/>}
                    </button>
                  </div>
                </div>

                {/* Error */}
                {error && <div className="flex items-center gap-2 px-3.5 py-2.5 rounded-xl bg-urgent-bg text-[12px] text-urgent border border-urgent/10">{error}</div>}

                {/* Submit */}
                <button type="submit" disabled={loading}
                  className="w-full flex items-center justify-center gap-2.5 py-3.5 text-[13.5px] font-semibold text-white rounded-xl transition-all duration-200 disabled:opacity-50 mt-2 hover:shadow-lg hover:shadow-signal/20 active:scale-[0.98]"
                  style={{background:'linear-gradient(135deg, #1F6FEB 0%, #4B8CFF 100%)'}}>
                  {loading ? <div className="w-4 h-4 border-[2px] border-white/30 border-l-white rounded-full animate-spin" /> : 'Sign in'}
                </button>
              </form>

              {/* Divider */}
              <div className="flex items-center gap-3 my-5">
                <div className="flex-1 h-px bg-line" />
                <span className="text-[10px] text-ink-faint uppercase tracking-widest">or</span>
                <div className="flex-1 h-px bg-line" />
              </div>

              {/* Demo access */}
              <button type="button" onClick={() => onLogin()}
                className="w-full flex items-center justify-center gap-2 py-3 text-[13px] font-medium text-ink-muted bg-surface-sunken hover:bg-line/50 rounded-xl transition-all duration-200 border border-line hover:border-line active:scale-[0.98]">
                <Zap size={14} className="text-signal" />
                Continue with demo access
              </button>
            </div>

            {/* Trust badges */}
            <div className="flex items-center justify-center gap-5 mt-6">
              <TrustBadge icon={<CheckCircle2 size={11}/>} text="SOC 2" />
              <TrustBadge icon={<CheckCircle2 size={11}/>} text="E2E Encrypted" />
              <TrustBadge icon={<CheckCircle2 size={11}/>} text="Buildathon 2026" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
function FeatureRow({ icon, title, desc }: { icon: React.ReactNode; title: string; desc: string }) {
  return (
    <div className="flex gap-3 items-start">
      <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5" style={{background:'rgba(255,255,255,0.08)',border:'1px solid rgba(255,255,255,0.06)'}}>
        <span className="text-blue-300/80">{icon}</span>
      </div>
      <div>
        <div className="text-[13px] font-medium text-white/90 tracking-tight">{title}</div>
        <div className="text-[11px] text-blue-200/45 mt-0.5 leading-relaxed">{desc}</div>
      </div>
    </div>
  );
}

function StatBlock({ value, label }: { value: string; label: string }) {
  return (
    <div>
      <div className="text-[22px] font-bold text-white tracking-tight font-data">{value}</div>
      <div className="text-[10px] text-blue-200/40 mt-0.5">{label}</div>
    </div>
  );
}

function TrustBadge({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div className="flex items-center gap-1.5 text-ink-faint">
      <span className="text-money">{icon}</span>
      <span className="text-[10px] font-medium tracking-wide">{text}</span>
    </div>
  );
}

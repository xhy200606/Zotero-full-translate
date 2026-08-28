const defaultBase = import.meta.env.VITE_API_BASE_URL || window.location.origin

function migrateLegacySameHostBase(){
  const stored = localStorage.getItem('zft_api_base')
  if (!stored) return
  try {
    const oldUrl = new URL(stored)
    const here = new URL(window.location.origin)
    if (oldUrl.hostname === here.hostname && ['8000','8089'].includes(oldUrl.port) && oldUrl.origin !== here.origin) {
      localStorage.setItem('zft_api_base', here.origin)
    }
  } catch {}
}
migrateLegacySameHostBase()

export const settings = {
  get base(){ return localStorage.getItem('zft_api_base') || defaultBase },
  set base(v){ localStorage.setItem('zft_api_base', String(v||'').replace(/\/$/, '')) },
  get key(){ return localStorage.getItem('zft_api_key') || '' },
  set key(v){ localStorage.setItem('zft_api_key', String(v||'')) },
  get theme(){ return localStorage.getItem('zft_theme') || 'system' },
  set theme(v){ localStorage.setItem('zft_theme', v) },
}

export async function api(path, options={}) {
  const headers = new Headers(options.headers || {})
  if (settings.key) headers.set('X-API-Key', settings.key)
  if (options.body && typeof options.body === 'string' && !headers.has('Content-Type')) headers.set('Content-Type','application/json')
  const res = await fetch(`${settings.base}${path}`, {...options, headers})
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
  const ct = res.headers.get('content-type') || ''
  return ct.includes('application/json') ? res.json() : res
}

export function eventUrl(jobId){
  return `${settings.base}/api/v1/jobs/${jobId}/events?token=${encodeURIComponent(settings.key)}`
}

export function applyTheme(mode){
  settings.theme=mode
  const dark = mode==='dark' || (mode==='system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
  document.documentElement.dataset.themeMode = mode
}

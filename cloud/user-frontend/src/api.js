const defaultBase = import.meta.env.VITE_API_BASE_URL || window.location.origin

const DEVICE_COOKIE='zft_browser_device'
function makeDeviceCode(){
  try { return crypto.randomUUID() } catch { return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}` }
}
function readDeviceCookie(){
  const prefix=`${DEVICE_COOKIE}=`
  for(const part of document.cookie.split(';')){const item=part.trim();if(item.startsWith(prefix)){try{return decodeURIComponent(item.slice(prefix.length))}catch{return item.slice(prefix.length)}}}
  return ''
}
function persistDeviceCode(value){
  const v=String(value||'').trim();if(!v)return ''
  localStorage.setItem('zft_device_code',v)
  document.cookie=`${DEVICE_COOKIE}=${encodeURIComponent(v)}; Max-Age=315360000; Path=/; SameSite=Lax${location.protocol==='https:'?'; Secure':''}`
  return v
}
function browserDeviceIdentity(){
  // Cookie scope ignores ports. Preserve the pre-upgrade localStorage UUID as
  // an alias before overwriting it, so the server can retire the duplicate
  // device previously created by the other web portal.
  const cookie=readDeviceCookie()
  const legacy=[localStorage.getItem('zft_device_code'),localStorage.getItem('zft_admin_device_code')].map(v=>String(v||'').trim()).filter(Boolean)
  const code=cookie||legacy[0]||makeDeviceCode()
  const aliases=[...new Set(legacy.filter(v=>v!==code))]
  persistDeviceCode(code)
  return {code,aliases}
}
const DEVICE_IDENTITY=browserDeviceIdentity()


export const settings = {
  get base(){ return localStorage.getItem('zft_api_base') || defaultBase },
  set base(v){ localStorage.setItem('zft_api_base', String(v||'').replace(/\/$/,'')) },
  get deviceCode(){ return DEVICE_IDENTITY.code },
  get deviceAliases(){ return DEVICE_IDENTITY.aliases },
  get theme(){ return localStorage.getItem('zft_theme') || 'system' },
  set theme(v){ localStorage.setItem('zft_theme',v) },
}

export async function api(path, options={}){
  const headers=new Headers(options.headers||{})
  if(options.body && typeof options.body==='string' && !headers.has('Content-Type')) headers.set('Content-Type','application/json')
  const res=await fetch(`${settings.base}${path}`,{...options,headers,credentials:'include'})
  if(res.status===401){const err=new Error('登录已失效，请重新登录');err.auth=true;throw err}
  if(!res.ok){ let detail=''; try{const j=await res.json();detail=j.detail||JSON.stringify(j)}catch{detail=await res.text()} const err=new Error(detail||`HTTP ${res.status}`);err.status=res.status;throw err }
  const ct=res.headers.get('content-type')||''
  return ct.includes('application/json')?res.json():res
}

export async function login(username,password){
  const payload={username,password,device_code:settings.deviceCode,device_aliases:settings.deviceAliases,device_name:'Web portal',platform:navigator.platform||navigator.userAgent,app_version:'web-2.5.2'}
  return api('/api/v1/auth/login',{method:'POST',body:JSON.stringify(payload)})
}

export async function register({username,password,email='',displayName=''}){
  const payload={username,password,email:email||null,display_name:displayName||null,device_code:settings.deviceCode,device_aliases:settings.deviceAliases,device_name:'Web portal',platform:navigator.platform||navigator.userAgent,app_version:'web-2.5.2'}
  return api('/api/v1/auth/register',{method:'POST',body:JSON.stringify(payload)})
}

export async function logout(){
  try{ await api('/api/v1/auth/logout',{method:'POST'}) }catch{}
}

export async function sessionUser(){
  return api('/api/v1/auth/me')
}

export function downloadUrl(jobId,kind='mono'){
  return `${settings.base}/api/v1/jobs/${encodeURIComponent(jobId)}/result/${kind}`
}

export async function downloadJob(jobId,kind='mono',filename='translated.pdf'){
  const res=await api(`/api/v1/jobs/${encodeURIComponent(jobId)}/result/${kind}`)
  const blob=await res.blob()
  const url=URL.createObjectURL(blob)
  const a=document.createElement('a');a.href=url;a.download=filename;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)
}

export function applyTheme(mode){
  settings.theme=mode
  const dark=mode==='dark'||(mode==='system'&&window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme=dark?'dark':'light'
  document.documentElement.dataset.themeMode=mode
}

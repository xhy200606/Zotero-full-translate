const defaultBase = import.meta.env.VITE_API_BASE_URL || window.location.origin

const DEVICE_COOKIE='zft_browser_device'
function makeDeviceCode(){try{return crypto.randomUUID()}catch{return `admin-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}}
function readDeviceCookie(){const prefix=`${DEVICE_COOKIE}=`;for(const part of document.cookie.split(';')){const item=part.trim();if(item.startsWith(prefix)){try{return decodeURIComponent(item.slice(prefix.length))}catch{return item.slice(prefix.length)}}}return ''}
function persistDeviceCode(value){const v=String(value||'').trim();if(!v)return '';localStorage.setItem('zft_admin_device_code',v);document.cookie=`${DEVICE_COOKIE}=${encodeURIComponent(v)}; Max-Age=315360000; Path=/; SameSite=Lax${location.protocol==='https:'?'; Secure':''}`;return v}
function browserDeviceIdentity(){const cookie=readDeviceCookie();const legacy=[localStorage.getItem('zft_admin_device_code'),localStorage.getItem('zft_device_code')].map(v=>String(v||'').trim()).filter(Boolean);const code=cookie||legacy[0]||makeDeviceCode();const aliases=[...new Set(legacy.filter(v=>v!==code))];persistDeviceCode(code);return {code,aliases}}
const DEVICE_IDENTITY=browserDeviceIdentity()


export const settings={
  get base(){return localStorage.getItem('zft_api_base')||defaultBase},
  set base(v){localStorage.setItem('zft_api_base',String(v||'').replace(/\/$/,''))},
  get key(){return localStorage.getItem('zft_api_key')||''},
  set key(v){localStorage.setItem('zft_api_key',String(v||''))},
  get deviceCode(){return DEVICE_IDENTITY.code},
  get deviceAliases(){return DEVICE_IDENTITY.aliases},
  get theme(){return localStorage.getItem('zft_theme')||'system'},
  set theme(v){localStorage.setItem('zft_theme',v)},
}

export async function api(path,options={}){
  const headers=new Headers(options.headers||{})
  if(options.serviceKey&&settings.key)headers.set('X-API-Key',settings.key)
  if(options.body&&typeof options.body==='string'&&!headers.has('Content-Type'))headers.set('Content-Type','application/json')
  const {serviceKey:_,...fetchOptions}=options
  const res=await fetch(`${settings.base}${path}`,{...fetchOptions,headers,credentials:'include'})
  if(res.status===401)throw new Error('管理员登录已失效，请重新登录')
  if(!res.ok){let detail='';try{const j=await res.json();detail=j.detail||JSON.stringify(j)}catch{detail=await res.text()}throw new Error(detail||`HTTP ${res.status}`)}
  const ct=res.headers.get('content-type')||''
  return ct.includes('application/json')?res.json():res
}

export async function adminLogin(username,password){
  const data=await api('/api/v1/auth/login',{method:'POST',body:JSON.stringify({username,password,device_code:settings.deviceCode,device_aliases:settings.deviceAliases,device_name:'Cloud Admin Console',platform:navigator.platform||navigator.userAgent,app_version:'admin-web-2.5.2'})})
  if(data?.user?.role!=='admin')throw new Error('该账户没有管理员权限')
  return data
}

export async function adminRegister({username,password,email='',displayName=''}){
  const data=await api('/api/v1/auth/register',{method:'POST',body:JSON.stringify({username,password,email:email||null,display_name:displayName||null,device_code:settings.deviceCode,device_aliases:settings.deviceAliases,device_name:'Cloud Admin Console',platform:navigator.platform||navigator.userAgent,app_version:'admin-web-2.5.2'})})
  if(data?.user?.role!=='admin')throw new Error('初始化失败：首个账户未获得管理员权限')
  return data
}

export async function adminSession(){return api('/api/v1/auth/me')}
export async function adminLogout(){try{await api('/api/v1/auth/logout',{method:'POST'})}catch{}}

export function applyTheme(mode){settings.theme=mode;const dark=mode==='dark'||(mode==='system'&&window.matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.dataset.theme=dark?'dark':'light';document.documentElement.dataset.themeMode=mode}

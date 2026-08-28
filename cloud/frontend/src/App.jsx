import React, {useEffect, useMemo, useState} from 'react'
import {api, applyTheme, eventUrl, settings} from './api'
import Navigation from './components/Navigation'
import Icon from './components/Icon'
import StatusBadge from './components/StatusBadge'
import MetricCard from './components/MetricCard'
import HealthPill from './components/HealthPill'
import LinearProgress from './components/LinearProgress'
import Switch from './components/Switch'

const terminal = new Set(['COMPLETED','FAILED','CANCELLED'])
const pageTitles = {
  overview:['总览',''],
  tasks:['任务',''],
  services:['翻译服务',''],
  runtime:['运行时',''],
  history:['翻译历史',''],
  settings:['设置',''],
}

function fmtTime(v){
  if(!v) return '—'
  try{return new Date(v).toLocaleString('zh-CN',{hour12:false})}catch{return String(v)}
}
function fmtCompact(v){ return new Intl.NumberFormat('zh-CN',{notation:'compact',maximumFractionDigits:1}).format(Number(v||0)) }
function fmtDuration(start,end){
  if(!start) return '—'
  const ms=(end?new Date(end):new Date())-new Date(start)
  if(!Number.isFinite(ms)||ms<0) return '—'
  const s=Math.floor(ms/1000), m=Math.floor(s/60), h=Math.floor(m/60)
  return h?`${h}小时 ${m%60}分`:m?`${m}分 ${s%60}秒`:`${s}秒`
}
function activeJob(job){ return !terminal.has(job.status) }
function providerName(id, providers){ return providers.find(x=>x.id===id)?.display_name || id || '—' }
function jobProviderName(job,providers){ const ids=job?.provider_ids?.length?job.provider_ids:[job?.provider]; return ids.length>1?`多引擎 · ${ids.length} 个（${ids.map(x=>providerName(x,providers)).join(' / ')}）`:providerName(ids[0],providers) }
function providerMark(kind){ return ({baidu:'百',openai_compatible:'AI',tencent:'腾',volcengine:'火',aliyun:'阿'})[kind]||'译' }

function TopAppBar({view,onRefresh,onCreate,connected}){
  const [title,subtitle]=pageTitles[view]||pageTitles.overview
  return <header className="md-top-app-bar">
    <div className="top-title"><h1>{title}</h1>{subtitle&&<p>{subtitle}</p>}</div>
    <div className="top-actions">
      <span className={`connection-state ${connected?'online':'offline'}`}><span/>{connected?'已连接':'未连接'}</span>
      <button className="icon-button" title="刷新" aria-label="刷新" onClick={onRefresh}><Icon name="refresh"/></button>
      <button className="md-button filled top-create" onClick={onCreate}><span className="button-plus">＋</span>新建翻译</button>
    </div>
  </header>
}

function EmptyState({title,body,action}){
  return <div className="empty-state"><div className="empty-icon"><Icon name="tasks" size={28}/></div><h3>{title}</h3>{body&&<p>{body}</p>}{action}</div>
}

function OverviewPage({jobs,status,providers,onOpenJob,onGoTasks}){
  const active=jobs.filter(activeJob)
  const latest=jobs.slice(0,6)
  const qps=status?.server_limits?.aggregate_qps_cap ?? status?.server_limits?.babeldoc_qps ?? '—'
  const defaultMetric=status?.provider_metrics?.find(x=>x.provider===status?.translator_provider)
  return <div className="page-stack">
    <section className="overview-hero">
      <div><span className="eyebrow">Zotero-full-translate Cloud 1.4.1</span><h2>PDF 翻译</h2></div>
      <div className="hero-state"><span>默认引擎</span><strong>{providerName(status?.translator_provider,providers)}</strong><small>{defaultMetric?`过去 60 秒 ${defaultMetric.requests_last_60s} 次请求`:'等待指标'}</small></div>
    </section>

    <section className="metric-grid">
      <MetricCard icon="tasks" label="运行 / 排队" value={(status?.active_jobs||0)+(status?.queued_jobs||0)} tone="primary"/>
      <MetricCard icon="check" label="累计完成" value={status?.completed_jobs ?? 0} tone="success"/>
      <MetricCard icon="warning" label="失败任务" value={status?.failed_jobs ?? 0} tone={status?.failed_jobs?'error':'surface'}/>
      <MetricCard icon="services" label="全局 QPS" value={qps} tone="secondary"/>
    </section>

    <section className="supporting-grid">
      <article className="md-card filled health-card">
        <div className="section-heading"><div><span className="overline">基础设施</span><h3>服务健康</h3></div><span className={`large-health ${status?.ok?'ok':'bad'}`}>{status?.ok?'全部正常':'需要检查'}</span></div>
        <div className="health-list">
          <HealthPill label="SQLite" ok={status?.database}/><HealthPill label="内置队列" ok={status?.redis}/><HealthPill label="本地存储" ok={status?.storage}/>
        </div>
        <div className="runtime-strip"><span>最大活动任务 <b>{status?.server_limits?.max_active_jobs ?? '—'}</b></span><span>Pool <b>{status?.server_limits?.pool_max_workers ?? '—'}</b></span><span>拆分页数 <b>{status?.server_limits?.max_pages_per_part ?? '—'}</b></span></div>
      </article>

      <article className="md-card filled provider-summary">
        <div className="section-heading"><div><span className="overline">翻译服务</span><h3>请求速率</h3></div></div>
        <div className="provider-mini-list">
          {(status?.provider_metrics||[]).map(m=><div className="provider-mini" key={m.provider}>
            <div><strong>{providerName(m.provider,providers)}</strong><small>{m.enabled?'已启用':'已停用'}</small></div>
            <div className="mini-metric"><b>{m.effective_qps}</b><span>/ {m.qps_limit} QPS</span></div>
            <LinearProgress value={Math.min(100,(Number(m.effective_qps||0)/Math.max(1,Number(m.qps_limit||1)))*100)}/>
          </div>)}
          {!status?.provider_metrics?.length&&<span className="support-text">等待服务指标…</span>}
        </div>
      </article>
    </section>

    <section className="md-card task-preview-card">
      <div className="section-heading"><div><span className="overline">最近任务</span><h3>翻译活动</h3></div><button className="md-button text" onClick={onGoTasks}>查看全部 <Icon name="chevron" size={18}/></button></div>
      <div className="recent-list">
        {latest.map(j=><button className="recent-row" key={j.id} onClick={()=>onOpenJob(j)}>
          <StatusBadge status={j.status}/><div className="recent-file"><strong>{j.filename}</strong><span>{j.stage} · {jobProviderName(j,providers)}</span></div><div className="recent-progress"><LinearProgress value={j.progress}/><small>{Number(j.progress||0).toFixed(0)}%</small></div><span className="recent-time">{fmtTime(j.created_at)}</span><Icon name="chevron" size={18}/>
        </button>)}
        {!latest.length&&<EmptyState title="还没有翻译任务"/>}
      </div>
    </section>
  </div>
}

function TaskDetail({job,timeline,providers,onCancel,onRetry,onDownload,onBack}){
  if(!job) return <EmptyState title="选择一个任务"/>
  const canCancel=activeJob(job)
  const canRetry=['FAILED','CANCELLED'].includes(job.status)
  return <div className="task-detail-content">
    <div className="detail-top-row"><button className="icon-button compact-back" onClick={onBack} aria-label="返回任务列表"><Icon name="back"/></button><StatusBadge status={job.status}/><span className="job-id">{job.id.slice(0,12)}</span></div>
    <h2 className="detail-file-title">{job.filename}</h2>
    <p className="detail-support">{job.stage} · {jobProviderName(job,providers)} · {job.lang_in} → {job.lang_out}</p>
    <div className="detail-progress-block"><div><strong>{Number(job.progress||0).toFixed(1)}%</strong><span>总进度</span></div><LinearProgress value={job.progress}/></div>

    <div className="detail-actions">
      {job.has_mono&&<button className="md-button filled" onClick={()=>onDownload(job,'mono')}><Icon name="download" size={18}/>单语 PDF</button>}
      {job.has_dual&&<button className="md-button tonal" onClick={()=>onDownload(job,'dual')}><Icon name="download" size={18}/>双语 PDF</button>}
      {canRetry&&<button className="md-button tonal" onClick={()=>onRetry(job)}><Icon name="retry" size={18}/>重新执行</button>}
      {canCancel&&<button className="md-button text danger-text" onClick={()=>onCancel(job)}><Icon name="cancel" size={18}/>取消任务</button>}
    </div>

    <div className="detail-info-grid">
      <div><span>创建时间</span><b>{fmtTime(job.created_at)}</b></div><div><span>运行耗时</span><b>{fmtDuration(job.started_at,job.finished_at)}</b></div>
      <div><span>Worker</span><b>{job.worker_name||'等待分配'}</b></div><div><span>服务限制</span><b>{job.qps} QPS · {job.pool_workers} workers</b></div>
      <div><span>客户端</span><b>{job.client_id||'Web / 未标记'}</b></div><div><span>Zotero Item</span><b>{job.client_item_key||'—'}</b></div>
    </div>

    {job.error_message&&<div className="error-container"><Icon name="warning"/><div><strong>{job.error_code||'任务失败'}</strong><p>{job.error_message}</p></div></div>}

    <section className="timeline-section"><div className="section-heading"><div><span className="overline">事件日志</span><h3>任务时间线</h3></div><span className="support-text">{timeline.length} 条</span></div>
      <ol className="timeline">
        {[...timeline].reverse().map((ev,index)=><li key={ev.id||`${ev.created_at}-${index}`} className={ev.event_type==='error'?'event-error':''}>
          <span className="timeline-node"/><div className="timeline-copy"><div><strong>{ev.stage||ev.event_type}</strong>{ev.status&&<span>{ev.status}</span>}</div><p>{ev.event_type==='error'?(ev.payload?.error_message||'发生错误'):ev.progress!=null?`总进度 ${Number(ev.progress).toFixed(1)}%`:(ev.payload?.stage_current!=null?`${ev.payload.stage_current} / ${ev.payload.stage_total||'?'}`:'状态更新')}</p><time>{fmtTime(ev.created_at)}</time></div>
        </li>)}
        {!timeline.length&&<li className="timeline-empty">等待事件…</li>}
      </ol>
    </section>
  </div>
}

function TasksPage({jobs,selected,setSelected,timeline,providers,onCancel,onRetry,onDownload}){
  const [query,setQuery]=useState('')
  const [filter,setFilter]=useState('all')
  const filtered=useMemo(()=>jobs.filter(j=>{
    const matches=!query||`${j.filename} ${j.id} ${j.client_item_key||''}`.toLowerCase().includes(query.toLowerCase())
    const state=filter==='all'||(filter==='active'&&activeJob(j))||(filter==='completed'&&j.status==='COMPLETED')||(filter==='failed'&&j.status==='FAILED')||(filter==='cancelled'&&j.status==='CANCELLED')
    return matches&&state
  }),[jobs,query,filter])
  return <section className={`task-list-detail ${selected?'detail-active':''}`}>
    <div className="task-list-pane">
      <div className="task-toolbar">
        <label className="search-field"><Icon name="search" size={20}/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="搜索文件、任务 ID、Zotero Item"/><button className="clear-search" onClick={()=>setQuery('')} aria-label="清除">{query?'×':''}</button></label>
        <div className="filter-chips" role="group" aria-label="任务筛选">
          {[['all','全部'],['active','运行中'],['completed','完成'],['failed','失败'],['cancelled','取消']].map(([id,label])=><button key={id} className={`filter-chip ${filter===id?'selected':''}`} onClick={()=>setFilter(id)}>{label}</button>)}
        </div>
      </div>
      <div className="task-list">
        {filtered.map(j=><button key={j.id} className={`task-list-item ${selected?.id===j.id?'selected':''}`} onClick={()=>setSelected(j)}>
          <div className="task-item-head"><StatusBadge status={j.status}/><time>{fmtTime(j.created_at)}</time></div>
          <strong className="task-item-title">{j.filename}</strong><span className="task-item-support">{j.stage} · {jobProviderName(j,providers)}</span>
          <div className="task-item-progress"><LinearProgress value={j.progress}/><small>{Number(j.progress||0).toFixed(0)}%</small></div>
        </button>)}
        {!filtered.length&&<EmptyState title="没有匹配任务"/>}
      </div>
    </div>
    <aside className="task-detail-pane">
      <TaskDetail job={selected} timeline={timeline} providers={providers} onCancel={onCancel} onRetry={onRetry} onDownload={onDownload} onBack={()=>setSelected(null)}/>
    </aside>
  </section>
}

function ServicesPage({providers,runtime,onEdit,onTest}){
  return <div className="page-stack">
    <section className="provider-grid">
      {providers.map(p=>{
        const m=p.metrics||{}, q=p.quota||{}, usage=Math.min(100,(Number(m.effective_qps||0)/Math.max(1,Number(m.qps_limit||1)))*100)
        const quotaPct=q.remaining_percent==null?null:Number(q.remaining_percent)
        return <article className={`provider-card md-card ${p.enabled?'enabled':'disabled'}`} key={p.id}>
          <div className="provider-card-head"><div className="provider-logo">{providerMark(p.kind)}</div><div className="provider-title"><h3>{p.display_name}</h3></div><span className={`availability-dot ${p.enabled&&p.configured?'ok':'off'}`}/></div>
          <div className="provider-state-row"><span className={`state-label ${p.enabled?'on':'off'}`}>{p.enabled?'已启用':'已停用'}</span><span>{p.configured?'凭据已配置':'凭据未完成'}</span>{p.last_test_ok!=null&&<span className={p.last_test_ok?'test-ok':'test-bad'}>{p.last_test_ok?'最近测试通过':'最近测试失败'}</span>}</div>
          <div className="qps-visual"><div><strong>{m.effective_qps??0}</strong><span>实时 QPS</span></div><div className="qps-bar"><LinearProgress value={usage}/><small>上限 {m.qps_limit??runtime?.babeldoc_qps??'—'}</small></div></div>
          <div className="provider-stats"><div><span>60 秒请求</span><b>{m.requests_last_60s??0}</b></div><div><span>60 秒错误</span><b>{m.errors_last_60s??0}</b></div><div><span>今日字符</span><b>{fmtCompact(m.today_characters??0)}</b></div></div>
          <div className={`quota-strip quota-${q.status||'unknown'}`}><div><span>额度</span><strong>{q.remaining_chars==null?'未配置总额度':`${fmtCompact(q.remaining_chars)} 剩余`}</strong></div>{quotaPct!=null&&<div className="quota-progress"><LinearProgress value={quotaPct}/><small>{quotaPct.toFixed(1)}%</small></div>}</div>
          {(q.last_error||m.last_error)&&<div className="provider-error"><Icon name="warning" size={17}/><span>{q.last_error||m.last_error?.message}</span></div>}
          <div className="provider-actions"><button className="md-button tonal" onClick={()=>onEdit(p)}><Icon name="tune" size={18}/>配置</button><button className="md-button text" disabled={!p.enabled||!p.configured} onClick={()=>onTest(p)}>测试连接</button></div>
        </article>
      })}
    </section>
  </div>
}

function RuntimePage({status,runtime,setRuntime,providers,workers,onSave}){
  if(!runtime) return <EmptyState title="正在读取运行时配置"/>
  const enabledProviders=providers.filter(x=>x.enabled&&x.configured)
  const defaultPool=(runtime.default_provider_ids?.length?runtime.default_provider_ids:[runtime.default_provider]).filter(Boolean)
  function toggleDefaultProvider(id){
    const next=defaultPool.includes(id)?defaultPool.filter(x=>x!==id):[...defaultPool,id]
    if(!next.length) return
    setRuntime({...runtime,default_provider_ids:next,default_provider:next[0]})
  }
  return <div className="runtime-layout">
    <section className="page-stack">
      <article className="md-card runtime-editor">
        <div className="section-heading"><div><h3>运行时硬限制</h3></div><button className="md-button filled" onClick={onSave}>保存运行时配置</button></div>
        
        <div className="default-engine-pool">
          <div className="engine-picker-head"><div><span className="overline">默认引擎池</span><strong>{defaultPool.length} 个引擎</strong></div><label className="compact-select"><span>默认策略</span><select value={runtime.default_provider_strategy||'balanced'} disabled={defaultPool.length<2} onChange={e=>setRuntime({...runtime,default_provider_strategy:e.target.value})}><option value="balanced">速度优先 · 智能并行</option><option value="failover">主备模式 · 失败切换</option></select></label></div>
          <div className="engine-picker runtime-engine-picker">{enabledProviders.map(p=>{const selected=defaultPool.includes(p.id);return <button type="button" key={p.id} className={`engine-choice ${selected?'selected':''}`} onClick={()=>toggleDefaultProvider(p.id)}><span className="engine-check">{selected?'✓':''}</span><span className="engine-choice-main"><strong>{p.display_name}</strong><small>{p.config?.qps||1} QPS · 并发 {p.config?.max_concurrency||1}</small></span></button>})}</div>
        </div>
        <div className="field-grid">
          <label className="outlined-field"><span>最大活动任务</span><input type="number" min="1" max="64" value={runtime.max_active_jobs} onChange={e=>setRuntime({...runtime,max_active_jobs:Number(e.target.value)})}/></label>
          <label className="outlined-field"><span>单引擎默认 QPS</span><input type="number" min="1" max="1000" value={runtime.babeldoc_qps} onChange={e=>setRuntime({...runtime,babeldoc_qps:Number(e.target.value)})}/></label>
          <label className="outlined-field"><span>聚合 QPS 安全上限</span><input type="number" min="1" max="5000" value={runtime.aggregate_qps_cap??100} onChange={e=>setRuntime({...runtime,aggregate_qps_cap:Number(e.target.value)})}/></label>
          <label className="outlined-field"><span>单引擎 Pool workers</span><input type="number" min="1" max="1000" value={runtime.pool_max_workers} onChange={e=>setRuntime({...runtime,pool_max_workers:Number(e.target.value)})}/></label>
          <label className="outlined-field"><span>多引擎 Pool workers</span><input type="number" min="1" max="1000" value={runtime.multi_pool_max_workers??12} onChange={e=>setRuntime({...runtime,multi_pool_max_workers:Number(e.target.value)})}/></label>
          <label className="outlined-field"><span>进度上报间隔（秒）</span><input type="number" min="0.1" step="0.1" value={runtime.report_interval} onChange={e=>setRuntime({...runtime,report_interval:Number(e.target.value)})}/></label>
          <label className="outlined-field"><span>每部分最大页数</span><input type="number" min="1" max="500" value={runtime.max_pages_per_part} onChange={e=>setRuntime({...runtime,max_pages_per_part:Number(e.target.value)})}/></label>
        </div>
        <div className="switch-group"><Switch label="额度感知调度" checked={runtime.quota_aware_dispatch!==false} onChange={v=>setRuntime({...runtime,quota_aware_dispatch:v})}/><Switch label="跳过扫描件检测" checked={runtime.skip_scanned_detection} onChange={v=>setRuntime({...runtime,skip_scanned_detection:v})}/><Switch label="自动启用 OCR workaround" checked={runtime.auto_ocr_workaround} onChange={v=>setRuntime({...runtime,auto_ocr_workaround:v})}/></div>
      </article>

      <article className="md-card worker-panel"><div className="section-heading"><div><h3>任务 Worker</h3></div><span className="support-text">{workers.length} 个在线</span></div>
        <div className="worker-list">{workers.map(w=><div className="worker-row" key={w.name}><span className="worker-icon"><Icon name="runtime" size={20}/></span><div className="worker-main"><strong>{w.name}</strong><span>PID {w.stats?.pid??'—'} · uptime {w.stats?.uptime??'—'}s</span></div><div className="worker-count"><b>{w.active_count}</b><span>活动</span></div><div className="worker-count"><b>{w.reserved_count}</b><span>预留</span></div><span className="status-chip tone-success"><span className="status-dot"/>在线</span></div>)}{!workers.length&&<EmptyState title="未发现 Worker"/>}</div>
      </article>
    </section>

    <aside className="runtime-support">
      <article className="md-card"><span className="overline">队列</span><h3 className="support-big">{status?.queue_depth??'—'}</h3></article>
      <article className="md-card"><span className="overline">服务</span><div className="support-health-stack"><HealthPill label="SQLite" ok={status?.database}/><HealthPill label="内置队列" ok={status?.redis}/><HealthPill label="本地存储" ok={status?.storage}/></div></article>
      
    </aside>
  </div>
}

function HistoryPage(){
  const [docs,setDocs]=useState([]),[tm,setTm]=useState(null),[error,setError]=useState('')
  useEffect(()=>{let alive=true;(async()=>{try{const [d,m]=await Promise.all([api('/api/v1/history/documents?limit=100'),api('/api/v1/history/translation-memory?limit=50')]);if(alive){setDocs(d.items||[]);setTm(m)}}catch(e){if(alive)setError(String(e.message||e))}})();return()=>{alive=false}},[])
  return <div className="page-stack">
    <section className="metric-grid history-metrics"><MetricCard icon="history" label="已记录 PDF" value={docs.length}/><MetricCard icon="services" label="文本记录" value={tm?.stats?.entries??'—'}/><MetricCard icon="tasks" label="已缓存原文字符" value={fmtCompact(tm?.stats?.stored_source_chars??0)}/></section>
    {error&&<section className="md-card provider-error"><Icon name="warning"/><span>{error}</span></section>}
    <section className="md-card"><div className="section-heading"><div><h3>已完成 PDF 历史</h3></div></div><div className="history-table">{docs.map(d=><div className="history-row" key={d.job_id}><div><strong>{d.filename}</strong><span>{d.lang_in} → {d.lang_out} · {(d.provider_ids||[]).join(' + ')}</span></div><div><b>复用 {d.reuse_count||0}</b><span>{String(d.source_sha256||'').slice(0,12)}…</span></div></div>)}{!docs.length&&<EmptyState title="暂无 PDF 历史"/>}</div></section>
    <section className="md-card"><div className="section-heading"><div><h3>最近文本记录</h3></div></div><div className="history-table">{(tm?.items||[]).map(x=><div className="history-row tm-row" key={x.id}><div><strong>{String(x.source_text||'').slice(0,120)}</strong><span>{x.lang_in} → {x.lang_out} · {x.provider_id||'历史缓存'}</span></div><div><b>命中 {x.hit_count||0}</b><span>{String(x.translated_text||'').slice(0,80)}</span></div></div>)}{tm&&!tm.items?.length&&<EmptyState title="暂无文本缓存"/>}</div></section>
  </div>
}

function SettingsPage({base,setBase,key,setKey,theme,setTheme,onSave}){
  return <div className="settings-grid">
    <article className="md-card settings-card"><div className="section-heading"><div><span className="overline">连接</span><h3>Zotero-full-translate Cloud API</h3></div><Icon name="key"/></div>
      <label className="outlined-field full"><span>API 地址</span><input value={base} onChange={e=>setBase(e.target.value)} placeholder="https://zft.example.com"/></label>
      <label className="outlined-field full"><span>API Key</span><input type="password" value={key} onChange={e=>setKey(e.target.value)} placeholder="zft api key"/></label>
      <button className="md-button filled" onClick={onSave}>保存并重新连接</button>
    </article>
    <article className="md-card settings-card"><div className="section-heading"><div><span className="overline">外观</span><h3>Material Design 3</h3></div><Icon name={theme==='dark'?'dark':'light'}/></div>
      <div className="segmented-control" role="group" aria-label="主题模式">{[['system','跟随系统'],['light','浅色'],['dark','深色']].map(([id,label])=><button key={id} className={theme===id?'selected':''} onClick={()=>{setTheme(id);applyTheme(id)}}>{label}</button>)}</div>
      <div className="theme-preview"><span className="preview-primary"/><span className="preview-secondary"/><span className="preview-tertiary"/><span className="preview-surface"/></div>
    </article>
  </div>
}

function ProviderSheet({provider,onClose,onSaved,notify}){
  const [enabled,setEnabled]=useState(provider?.enabled||false)
  const [displayName,setDisplayName]=useState(provider?.display_name||'')
  const [config,setConfig]=useState(provider?.config||{})
  const [secrets,setSecrets]=useState({})
  useEffect(()=>{ if(provider){setEnabled(provider.enabled);setDisplayName(provider.display_name);setConfig(provider.config||{});setSecrets({})} },[provider?.id])
  if(!provider) return null
  const secret=(key,label,placeholder)=><label className="outlined-field full"><span>{label}</span><input type="password" value={secrets[key]||''} onChange={e=>setSecrets({...secrets,[key]:e.target.value})} placeholder={provider.secret_fields?.[key]?'已配置；留空保持不变':placeholder}/></label>
  const commonLimits=<><div className="field-grid compact"><label className="outlined-field"><span>QPS 上限</span><input type="number" min="0.1" step="0.1" value={config.qps??1} onChange={e=>setConfig({...config,qps:Number(e.target.value)})}/></label><label className="outlined-field"><span>最大并发</span><input type="number" min="1" value={config.max_concurrency??1} onChange={e=>setConfig({...config,max_concurrency:Number(e.target.value)})}/></label></div><div className="quota-config-block"><Switch label="启用额度感知" checked={config.quota_enabled!==false} onChange={v=>setConfig({...config,quota_enabled:v})}/><div className="field-grid compact"><label className="outlined-field"><span>周期总字符</span><input type="number" min="0" step="1000" value={config.quota_total_chars??0} onChange={e=>setConfig({...config,quota_total_chars:Number(e.target.value)})}/></label><label className="outlined-field"><span>保留字符</span><input type="number" min="0" step="1000" value={config.quota_reserve_chars??0} onChange={e=>setConfig({...config,quota_reserve_chars:Number(e.target.value)})}/></label><label className="outlined-field"><span>低额度阈值 %</span><input type="number" min="1" max="99" value={config.quota_low_percent??10} onChange={e=>setConfig({...config,quota_low_percent:Number(e.target.value)})}/></label><label className="outlined-field"><span>额度周期</span><select value={config.quota_period||'month'} onChange={e=>setConfig({...config,quota_period:e.target.value})}><option value="month">每月重置</option><option value="account">账户总量</option></select></label></div></div></>
  async function save(){
    try{const updated=await api(`/api/v1/providers/${provider.id}`,{method:'PUT',body:JSON.stringify({enabled,display_name:displayName,config,secrets})});notify('翻译服务配置已保存','success');onSaved(updated);onClose()}catch(e){notify(String(e.message||e),'error')}
  }
  return <div className="sheet-layer" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><aside className="side-sheet" role="dialog" aria-modal="true" aria-label="翻译服务配置">
    <div className="sheet-head"><div><span className="overline">翻译服务</span><h2>{provider.display_name}</h2></div><button className="icon-button" onClick={onClose}><Icon name="close"/></button></div>
    <Switch label="启用此服务" checked={enabled} onChange={setEnabled}/>
    <label className="outlined-field full"><span>显示名称</span><input value={displayName} onChange={e=>setDisplayName(e.target.value)}/></label>
    {commonLimits}
    {provider.kind==='openai_compatible'&&<>
      <label className="outlined-field full"><span>Base URL</span><input value={config.base_url||''} onChange={e=>setConfig({...config,base_url:e.target.value})} placeholder="https://api.openai.com/v1"/></label>
      <label className="outlined-field full"><span>模型</span><input value={config.model||''} onChange={e=>setConfig({...config,model:e.target.value})} placeholder="gpt-4.1-mini"/></label>
      {secret('api_key','API Key','输入 API Key')}
    </>}
    {provider.kind==='baidu'&&<>
      <label className="outlined-field full"><span>鉴权方式</span><select value={config.auth_mode||'sign'} onChange={e=>{const mode=e.target.value;setConfig({...config,auth_mode:mode,endpoint:mode==='api_key'?'https://fanyi-api.baidu.com/ait/api/aiTextTranslate':'https://fanyi-api.baidu.com/api/trans/vip/translate'})}}><option value="sign">APPID + 开发者密钥（通用翻译）</option><option value="api_key">API Key / Bearer（大模型文本翻译）</option></select></label>
      <label className="outlined-field full"><span>百度接口</span><input value={config.endpoint||''} onChange={e=>setConfig({...config,endpoint:e.target.value})}/></label>
      {secret('app_id','APP ID','输入百度翻译 APPID')}
      {(config.auth_mode||'sign')==='api_key'?<>{secret('api_key','API Key','输入“API Key管理”创建的 API Key')}<label className="outlined-field full"><span>模型类型</span><select value={config.model_type||'llm'} onChange={e=>setConfig({...config,model_type:e.target.value})}><option value="llm">LLM 大模型翻译</option><option value="nmt">NMT 机器翻译</option></select></label><label className="outlined-field full"><span>翻译指令（可选）</span><input value={config.reference||''} onChange={e=>setConfig({...config,reference:e.target.value})}/></label></>:secret('secret_key','开发者密钥','输入“开发者信息”页面中的密钥')}
    </>}
    {provider.kind==='tencent'&&<>
      <label className="outlined-field full"><span>接入方式</span><select value={config.auth_mode||'tmt_tc3'} onChange={e=>setConfig({...config,auth_mode:e.target.value})}><option value="tmt_tc3">腾讯机器翻译 TMT · SecretId/SecretKey</option><option value="tokenhub">TokenHub Hy-MT2 · API Key</option><option value="hunyuan_tc3">混元 ChatTranslations · SecretId/SecretKey</option></select></label>
      {(config.auth_mode||'tmt_tc3')==='tmt_tc3'?<>
        
        <label className="outlined-field full"><span>TMT Endpoint</span><input value={config.tmt_endpoint||'https://tmt.tencentcloudapi.com'} onChange={e=>setConfig({...config,tmt_endpoint:e.target.value})}/></label>
        <div className="field-grid compact"><label className="outlined-field"><span>Region</span><input value={config.tmt_region||'ap-beijing'} onChange={e=>setConfig({...config,tmt_region:e.target.value})}/></label><label className="outlined-field"><span>API Version</span><input value={config.tmt_version||'2018-03-21'} onChange={e=>setConfig({...config,tmt_version:e.target.value})}/></label></div>
        <div className="field-grid compact"><label className="outlined-field"><span>ProjectId</span><input type="number" min="0" value={config.project_id??0} onChange={e=>setConfig({...config,project_id:Number(e.target.value)})}/></label><label className="outlined-field"><span>单次字符上限</span><input type="number" min="200" max="1950" value={config.max_chars||1900} onChange={e=>setConfig({...config,max_chars:Number(e.target.value)})}/></label></div>
        {secret('secret_id','SecretId','腾讯云 API SecretId')}{secret('secret_key','SecretKey','腾讯云 API SecretKey')}
      </>:(config.auth_mode==='hunyuan_tc3'?<>
        <label className="outlined-field full"><span>混元 Endpoint</span><input value={config.hunyuan_endpoint||'https://hunyuan.ai.tencentcloudapi.com'} onChange={e=>setConfig({...config,hunyuan_endpoint:e.target.value})}/></label>
        <label className="outlined-field full"><span>混元翻译模型</span><select value={config.hunyuan_model||'hunyuan-translation-lite'} onChange={e=>setConfig({...config,hunyuan_model:e.target.value})}><option value="hunyuan-translation-lite">hunyuan-translation-lite</option><option value="hunyuan-translation">hunyuan-translation</option></select></label>
        <label className="outlined-field full"><span>领域（可选）</span><input value={config.field||''} onChange={e=>setConfig({...config,field:e.target.value})} placeholder="学术论文"/></label>
        {secret('secret_id','SecretId','腾讯云 API SecretId')}{secret('secret_key','SecretKey','腾讯云 API SecretKey')}
      </>:<>
        <label className="outlined-field full"><span>TokenHub Base URL</span><input value={config.base_url||''} onChange={e=>setConfig({...config,base_url:e.target.value})} placeholder="https://tokenhub.tencentmaas.com/v1"/></label>
        <label className="outlined-field full"><span>翻译模型</span><select value={config.model||'hy-mt2-plus'} onChange={e=>setConfig({...config,model:e.target.value})}><option value="hy-mt2-lite">hy-mt2-lite · 速度优先</option><option value="hy-mt2-plus">hy-mt2-plus · 均衡</option><option value="hy-mt2-pro">hy-mt2-pro · 质量优先</option></select></label>
        {secret('api_key','TokenHub API Key','腾讯云 TokenHub API Key')}
      </>)}
    </>}
    {provider.kind==='volcengine'&&<>
      <label className="outlined-field full"><span>Endpoint</span><input value={config.endpoint||'https://openspeech.bytedance.com/api/v3/machine_translation/matx_translate'} onChange={e=>setConfig({...config,endpoint:e.target.value})}/></label>
      <label className="outlined-field full"><span>Resource ID</span><input value={config.resource_id||'volc.speech.mt'} onChange={e=>setConfig({...config,resource_id:e.target.value})}/></label>
      {secret('api_key','API Key','输入火山机器翻译 API Key')}
    </>}
    {provider.kind==='aliyun'&&<>
      <label className="outlined-field full"><span>接口</span><input value={config.endpoint||''} onChange={e=>setConfig({...config,endpoint:e.target.value})} placeholder="https://mt.cn-hangzhou.aliyuncs.com"/></label>
      <label className="outlined-field full"><span>API Path</span><input value={config.path||'/api/translate/web/general'} onChange={e=>setConfig({...config,path:e.target.value})}/></label>
      <div className="field-grid compact"><label className="outlined-field"><span>Scene</span><input value={config.scene||'general'} onChange={e=>setConfig({...config,scene:e.target.value})}/></label><label className="outlined-field"><span>单次字符上限</span><input type="number" value={config.max_chars||4900} onChange={e=>setConfig({...config,max_chars:Number(e.target.value)})}/></label></div>
      {secret('access_key_id','AccessKey ID','阿里云 AccessKey ID')}{secret('access_key_secret','AccessKey Secret','阿里云 AccessKey Secret')}
    </>}
    
    <div className="sheet-actions"><button className="md-button text" onClick={onClose}>取消</button><button className="md-button filled" onClick={save}>保存配置</button></div>
  </aside></div>
}

function CreateJobDialog({providers,onClose,onCreated,notify}){
  const enabled=providers.filter(x=>x.enabled&&x.configured)
  const [file,setFile]=useState(null),[langIn,setLangIn]=useState('en'),[langOut,setLangOut]=useState('zh-CN'),[output,setOutput]=useState('mono'),[selectedProviders,setSelectedProviders]=useState(enabled.slice(0,1).map(x=>x.id)),[strategy,setStrategy]=useState('balanced'),[busy,setBusy]=useState(false)
  function toggleProvider(id){setSelectedProviders(old=>old.includes(id)?old.filter(x=>x!==id):[...old,id])}
  async function submit(e){
    e.preventDefault(); if(!file){notify('请选择 PDF 文件','error');return} if(!selectedProviders.length){notify('至少选择一个翻译引擎','error');return}
    const fd=new FormData();fd.append('file',file);fd.append('lang_in',langIn);fd.append('lang_out',langOut);fd.append('output_mode',output);fd.append('providers',selectedProviders.join(','));fd.append('provider_strategy',selectedProviders.length>1?strategy:'single');fd.append('client_id','zft-web-console');fd.append('client_request_id',crypto.randomUUID())
    try{setBusy(true);const job=await api('/api/v1/jobs',{method:'POST',body:fd});notify(selectedProviders.length>1?`已提交 ${selectedProviders.length} 引擎并行任务`:'任务已提交','success');onCreated(job);onClose()}catch(err){notify(String(err.message||err),'error')}finally{setBusy(false)}
  }
  const totalQps=enabled.filter(p=>selectedProviders.includes(p.id)).reduce((a,p)=>a+Number(p.config?.qps||1),0)
  return <div className="dialog-scrim" onMouseDown={e=>{if(e.target===e.currentTarget)onClose()}}><form className="md-dialog wide-dialog" onSubmit={submit}><div className="dialog-icon"><Icon name="tasks"/></div><h2>新建固定版式 PDF 翻译</h2>
    <label className="file-drop"><input type="file" accept="application/pdf" onChange={e=>setFile(e.target.files?.[0]||null)}/><span className="file-drop-icon">PDF</span><strong>{file?.name||'选择 PDF 文件'}</strong><small>{file?`${(file.size/1024/1024).toFixed(1)} MB`:'最大 200 MiB'}</small></label>
    <div className="field-grid compact"><label className="outlined-field"><span>源语言</span><input value={langIn} onChange={e=>setLangIn(e.target.value)}/></label><label className="outlined-field"><span>目标语言</span><input value={langOut} onChange={e=>setLangOut(e.target.value)}/></label><label className="outlined-field"><span>输出</span><select value={output} onChange={e=>setOutput(e.target.value)}><option value="mono">单语译文 PDF</option><option value="dual">双语 PDF</option><option value="both">两者都生成</option></select></label><label className="outlined-field"><span>调度策略</span><select value={strategy} disabled={selectedProviders.length<2} onChange={e=>setStrategy(e.target.value)}><option value="balanced">速度优先 · 智能并行</option><option value="failover">主备模式 · 失败切换</option></select></label></div>
    <div className="engine-picker-head"><div><span className="overline">翻译引擎池</span><strong>{selectedProviders.length} 个已选择</strong></div><span className="status-chip tone-primary">理论总 QPS {totalQps.toFixed(1)}</span></div>
    <div className="engine-picker">{enabled.map(p=>{const selected=selectedProviders.includes(p.id);return <button type="button" key={p.id} className={`engine-choice ${selected?'selected':''}`} onClick={()=>toggleProvider(p.id)}><span className="engine-check">{selected?'✓':''}</span><span className="engine-choice-main"><strong>{p.display_name}</strong><small>{p.config?.qps||1} QPS · 并发 {p.config?.max_concurrency||1}</small></span></button>})}{!enabled.length&&<div className="empty-inline">没有已启用且配置完成的翻译服务。</div>}</div>
    
    <div className="dialog-actions"><button type="button" className="md-button text" onClick={onClose}>取消</button><button className="md-button filled" disabled={busy||!file||!selectedProviders.length}>{busy?'正在上传…':'创建任务'}</button></div>
  </form></div>
}

export default function App(){
  const [view,setView]=useState('overview')
  const [jobs,setJobs]=useState([]),[status,setStatus]=useState(null),[providers,setProviders]=useState([]),[runtime,setRuntime]=useState(null),[workers,setWorkers]=useState([])
  const [selected,setSelected]=useState(null),[timeline,setTimeline]=useState([])
  const [error,setError]=useState(''),[toast,setToast]=useState(null)
  const [providerSheet,setProviderSheet]=useState(null),[createOpen,setCreateOpen]=useState(false)
  const [base,setBase]=useState(settings.base),[key,setKey]=useState(settings.key),[theme,setTheme]=useState(settings.theme)
  const connected=Boolean(settings.key)&&!error

  function notify(message,tone='info'){setToast({message,tone});window.clearTimeout(notify._t);notify._t=window.setTimeout(()=>setToast(null),3600)}

  async function refresh({quiet=false}={}){
    if(!settings.key){ if(!quiet)setError('请先在“设置”中填写 Zotero-full-translate Cloud API Key。'); return }
    try{
      const [j,s,p,r]=await Promise.all([api('/api/v1/jobs?limit=100'),api('/api/v1/system/status'),api('/api/v1/providers'),api('/api/v1/system/runtime')])
      setJobs(j.items);setStatus(s);setProviders(p);setRuntime(old=>old&&old._dirty?old:{...r,_dirty:false});setError('')
      if(selected){const next=j.items.find(x=>x.id===selected.id);if(next)setSelected(next)}
    }catch(e){setError(String(e.message||e));return}
    try{setWorkers(await api('/api/v1/system/workers'))}catch{setWorkers([])}
  }

  async function loadTimeline(jobId){
    if(!jobId||!settings.key)return
    try{setTimeline(await api(`/api/v1/jobs/${jobId}/timeline?limit=300`))}catch{}
  }

  useEffect(()=>{refresh();const t=setInterval(()=>refresh({quiet:true}),3500);return()=>clearInterval(t)},[selected?.id])
  useEffect(()=>{if(selected?.id)loadTimeline(selected.id);else setTimeline([])},[selected?.id])
  useEffect(()=>{
    if(!selected?.id||!settings.key)return
    const es=new EventSource(eventUrl(selected.id))
    es.onmessage=()=>{refresh({quiet:true});loadTimeline(selected.id)}
    return()=>es.close()
  },[selected?.id])

  async function cancel(job){if(!window.confirm(`取消“${job.filename}”？`))return;try{await api(`/api/v1/jobs/${job.id}`,{method:'DELETE'});notify('已发送取消请求','info');refresh()}catch(e){notify(String(e.message||e),'error')}}
  async function retry(job){try{const next=await api(`/api/v1/jobs/${job.id}/retry`,{method:'POST'});setSelected(next);notify('任务已重新排队','success');refresh()}catch(e){notify(String(e.message||e),'error')}}
  async function download(job,kind){try{const res=await api(`/api/v1/jobs/${job.id}/result/${kind}`);const blob=await res.blob();const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`${job.filename.replace(/\.pdf$/i,'')}.${kind}.pdf`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1200)}catch(e){notify(String(e.message||e),'error')}}
  async function testProvider(p){notify(`正在测试 ${p.display_name}…`);try{const result=await api(`/api/v1/providers/${p.id}/test`,{method:'POST'});notify(result.ok?`${p.display_name}：${result.sample||result.message}`:`${p.display_name}：${result.message}`,result.ok?'success':'error');refresh()}catch(e){notify(String(e.message||e),'error')}}
  async function saveRuntime(){try{const payload={...runtime};delete payload.updated_at;delete payload._dirty;const next=await api('/api/v1/system/runtime',{method:'PUT',body:JSON.stringify(payload)});setRuntime({...next,_dirty:false});notify('运行时配置已保存；新任务立即生效','success');refresh()}catch(e){notify(String(e.message||e),'error')}}
  function updateRuntime(v){setRuntime({...v,_dirty:true})}
  function saveConnection(){settings.base=base;settings.key=key;setError('');notify('连接设置已保存','success');refresh()}
  function openJob(job){setSelected(job);setView('tasks')}

  let content
  if(view==='overview')content=<OverviewPage jobs={jobs} status={status} providers={providers} onOpenJob={openJob} onGoTasks={()=>setView('tasks')}/>
  else if(view==='tasks')content=<TasksPage jobs={jobs} selected={selected} setSelected={setSelected} timeline={timeline} providers={providers} onCancel={cancel} onRetry={retry} onDownload={download}/>
  else if(view==='services')content=<ServicesPage providers={providers} runtime={runtime} onEdit={setProviderSheet} onTest={testProvider}/>
  else if(view==='runtime')content=<RuntimePage status={status} runtime={runtime} setRuntime={updateRuntime} providers={providers} workers={workers} onSave={saveRuntime}/>
  else if(view==='history')content=<HistoryPage/>
  else content=<SettingsPage base={base} setBase={setBase} key={key} setKey={setKey} theme={theme} setTheme={setTheme} onSave={saveConnection}/>

  return <div className="md-app-shell">
    <Navigation view={view} onChange={setView}/>
    <div className="md-main-area">
      <TopAppBar view={view} onRefresh={()=>refresh()} onCreate={()=>setCreateOpen(true)} connected={connected}/>
      <main className={`md-content page-${view}`}>
        {error&&<div className="global-error"><Icon name="warning"/><div><strong>无法连接 Zotero-full-translate Cloud</strong><span>{error}</span></div><button className="md-button text" onClick={()=>setView('settings')}>打开设置</button></div>}
        {content}
      </main>
    </div>
    {toast&&<div className={`snackbar snackbar-${toast.tone}`}><Icon name={toast.tone==='error'?'warning':toast.tone==='success'?'check':'info'} size={18}/><span>{toast.message}</span><button onClick={()=>setToast(null)}>关闭</button></div>}
    {providerSheet&&<ProviderSheet provider={providerSheet} onClose={()=>setProviderSheet(null)} onSaved={()=>refresh()} notify={notify}/>} 
    {createOpen&&<CreateJobDialog providers={providers} onClose={()=>setCreateOpen(false)} onCreated={openJob} notify={notify}/>} 
  </div>
}

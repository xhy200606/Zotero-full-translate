import Icon from './Icon'

const items = [
  ['overview','dashboard','总览'], ['tasks','tasks','任务'], ['services','services','翻译服务'], ['runtime','runtime','运行时'], ['history','history','翻译历史'], ['settings','settings','设置']
]

function NavItem({item, current, onChange, compact=false}){
  const [id,icon,label]=item
  return <button className={`nav-item ${current===id?'active':''}`} onClick={()=>onChange(id)} aria-current={current===id?'page':undefined}>
    <span className="nav-indicator"><Icon name={icon} size={compact?23:24}/></span><span className="nav-label">{label}</span>
  </button>
}

export default function Navigation({view,onChange}){
  return <>
    <aside className="md-nav-drawer" aria-label="主导航">
      <div className="brand-block"><div className="brand-mark">Z</div><div><strong>Zotero-full-translate</strong><span>Cloud Translation</span></div></div>
      <nav>{items.map(x=><NavItem key={x[0]} item={x} current={view} onChange={onChange}/>)}</nav>
      <div className="drawer-footer"><span>BabelDOC Control Plane</span><small>v1.4</small></div>
    </aside>
    <aside className="md-nav-rail" aria-label="主导航">
      <div className="rail-logo">Z</div>
      <nav>{items.map(x=><NavItem key={x[0]} item={x} current={view} onChange={onChange}/>)}</nav>
    </aside>
    <nav className="md-nav-bar" aria-label="主导航">
      {items.map(x=><NavItem key={x[0]} item={x} current={view} onChange={onChange} compact/>)}
    </nav>
  </>
}

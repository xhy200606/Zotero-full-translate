export default function Switch({checked,onChange,label,disabled=false}){
  return <label className={`md-switch-row ${disabled?'disabled':''}`}><span>{label}</span><button type="button" className={`md-switch ${checked?'selected':''}`} aria-pressed={checked} onClick={()=>!disabled&&onChange(!checked)} disabled={disabled}><span className="switch-handle"/></button></label>
}

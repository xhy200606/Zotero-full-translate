import Icon from './Icon'
export default function MetricCard({icon,label,value,supporting,tone='surface'}){
  return <article className={`metric-card metric-${tone}`}>
    <div className="metric-icon"><Icon name={icon} size={22}/></div>
    <div className="metric-copy"><span>{label}</span><strong>{value}</strong>{supporting&&<small>{supporting}</small>}</div>
  </article>
}

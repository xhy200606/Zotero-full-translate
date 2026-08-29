import Icon from './Icon'
export default function HealthPill({label,ok,detail}){
  return <div className={`health-pill ${ok?'healthy':'unhealthy'}`}><Icon name={ok?'check':'warning'} size={17}/><span>{label}</span>{detail&&<small>{detail}</small>}</div>
}

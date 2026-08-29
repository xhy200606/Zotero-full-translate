export default function LinearProgress({value=0, labelled=false}){
  const n=Math.max(0,Math.min(100,Number(value||0)))
  return <div className="linear-progress-wrap"><div className="linear-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={n}><span style={{width:`${n}%`}}/></div>{labelled&&<small>{n.toFixed(1)}%</small>}</div>
}

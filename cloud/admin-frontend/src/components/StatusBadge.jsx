const labels = {
  QUEUED:'排队中', PARSING:'解析', TRANSLATING:'翻译', TYPESETTING:'排版', RENDERING:'生成 PDF',
  FINALIZING:'收尾', COMPLETED:'已完成', FAILED:'失败', CANCELLING:'取消中', CANCELLED:'已取消', RUNNING:'运行中'
}
export default function StatusBadge({status}){
  const tone = status==='COMPLETED' ? 'success' : status==='FAILED' ? 'error' : status==='CANCELLED' ? 'neutral' : status==='CANCELLING' ? 'warning' : 'primary'
  return <span className={`status-chip tone-${tone}`}><span className="status-dot"/>{labels[status] || status || '未知'}</span>
}

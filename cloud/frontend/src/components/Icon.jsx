const paths = {
  dashboard: 'M4 13h7V4H4v9Zm9 7h7v-7h-7v7ZM4 20h7v-5H4v5Zm9-9h7V4h-7v7Z',
  tasks: 'M7 3h10v2h3v16H4V5h3V3Zm2 2h6V4H9v1Zm-2 4v2h10V9H7Zm0 4v2h10v-2H7Zm0 4v2h7v-2H7Z',
  services: 'M12 2 4 6v12l8 4 8-4V6l-8-4Zm0 2.2L17.6 7 12 9.8 6.4 7 12 4.2ZM6 8.6l5 2.5v8.3l-5-2.5V8.6Zm7 10.8v-8.3l5-2.5v8.3l-5 2.5Z',
  runtime: 'M4 4h16v16H4V4Zm2 2v12h12V6H6Zm2 8 2.2-3 2 2.2L15.7 9 17 10.1l-4.7 5.7-2-2.2L9.2 15 8 14Z',
  history: 'M5 4h2v2H5v12h12v-2h2v4H3V4h2Zm4 0h10v10H9V4Zm2 2v6h6V6h-6Zm1 1h4v2h-4V7Z',
  settings: 'm19.4 13 .1-1-.1-1 2-1.5-2-3.5-2.4 1a7 7 0 0 0-1.7-1L15 3.4h-4L10.7 6A7 7 0 0 0 9 7L6.6 6 4.6 9.5 6.6 11l-.1 1 .1 1-2 1.5 2 3.5L9 17a7 7 0 0 0 1.7 1l.3 2.6h4l.3-2.6a7 7 0 0 0 1.7-1l2.4 1 2-3.5-2-1.5ZM13 16a4 4 0 1 1 0-8 4 4 0 0 1 0 8Z',
  refresh: 'M17.7 6.3A8 8 0 0 0 5.3 7.7L3 5.4V12h6.6L7 9.4a5.2 5.2 0 1 1-.1 5.3l-2.2 1.2A7.8 7.8 0 1 0 17.7 6.3Z',
  search: 'M10.5 4a6.5 6.5 0 1 0 4 11.6L19 20l1-1-4.4-4.5A6.5 6.5 0 0 0 10.5 4Zm0 2a4.5 4.5 0 1 1 0 9 4.5 4.5 0 0 1 0-9Z',
  download: 'M11 4h2v8l2.5-2.5 1.4 1.4-4.9 4.9-4.9-4.9 1.4-1.4L11 12V4ZM5 18h14v2H5v-2Z',
  close: 'm6.4 5 5.6 5.6L17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4-5.6-5.6L6.4 19 5 17.6l5.6-5.6L5 6.4 6.4 5Z',
  cancel: 'M7 7h10v10H7V7Zm-2-2v14h14V5H5Z',
  retry: 'M17.7 6.3A8 8 0 0 0 5.3 7.7L3 5.4V12h6.6L7 9.4a5.2 5.2 0 1 1-.1 5.3l-2.2 1.2A7.8 7.8 0 1 0 17.7 6.3Z',
  back: 'M20 11H7.8l4.6-4.6L11 5l-7 7 7 7 1.4-1.4L7.8 13H20v-2Z',
  dark: 'M20 15.5A8.5 8.5 0 0 1 8.5 4 7 7 0 1 0 20 15.5Z',
  light: 'M12 5a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm0-4h1v3h-1V1Zm0 19h1v3h-1v-3ZM1 12h3v1H1v-1Zm19 0h3v1h-3v-1ZM4.2 3.5l2.1 2.1-.7.7-2.1-2.1.7-.7Zm13.5 13.5 2.1 2.1-.7.7-2.1-2.1.7-.7ZM19.1 3.5l.7.7-2.1 2.1-.7-.7 2.1-2.1ZM5.6 17l.7.7-2.1 2.1-.7-.7L5.6 17Z',
  chevron: 'm9 5 7 7-7 7-1.4-1.4L13.2 12 7.6 6.4 9 5Z',
  tune: 'M4 7h10v2H4V7Zm12-2h2v6h-2V5ZM10 15h10v2H10v-2Zm-4-2h2v6H6v-6Z',
  key: 'M14 4a6 6 0 0 0-5.7 8H3v4h3v3h4v-3h2.3A6 6 0 1 0 14 4Zm0 2a4 4 0 1 1 0 8 4 4 0 0 1 0-8Z',
  check: 'm9.2 16.2-4-4L6.6 10.8l2.6 2.6 7.2-7.2 1.4 1.4-8.6 8.6Z',
  warning: 'M12 3 2.5 20h19L12 3Zm0 4 6.1 11H5.9L12 7Zm-1 3v4h2v-4h-2Zm0 5.5v2h2v-2h-2Z',
  info: 'M11 10h2v7h-2v-7Zm0-3h2v2h-2V7Zm1-4a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm0 2a7 7 0 1 1 0 14 7 7 0 0 1 0-14Z',
  more: 'M6 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4Zm6 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4Zm6 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z',
}

export default function Icon({name, size=24, className=''}){
  return <svg className={`icon ${className}`} width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d={paths[name] || paths.info}/></svg>
}

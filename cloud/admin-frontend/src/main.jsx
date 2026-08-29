import React from 'react'
import {createRoot} from 'react-dom/client'
import App from './App'
import {applyTheme, settings} from './api'
import './style.css'

applyTheme(settings.theme)
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{
  if(settings.theme==='system') applyTheme('system')
})

createRoot(document.getElementById('root')).render(<React.StrictMode><App/></React.StrictMode>)

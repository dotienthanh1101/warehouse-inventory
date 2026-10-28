const paths={
 box:'M12 3 3 8v9l9 5 9-5V8l-9-5Zm0 10v9M3 8l9 5 9-5M7.5 5.5l9 5',
 home:'m3 10 9-7 9 7M5 9v12h5v-7h4v7h5V9',
 camera:'M4 7h4l2-3h4l2 3h4v13H4V7Zm12 6a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
 scan:'M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M7 12h10',
 list:'M9 5h12M9 12h12M9 19h12M3 5h1M3 12h1M3 19h1',
 chart:'M4 3v17h17M8 16V9m5 7V5m5 11v-5',
 tag:'M3 3h8l10 10-8 8L3 11V3Zm4 4h.01',
 users:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m16-14a4 4 0 0 1 0 8m4 6v-2a4 4 0 0 0-3-3M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
 help:'M21 11a8 8 0 0 1-8 8H7l-5 3 1-6a8 8 0 1 1 18-5ZM9 8a3 3 0 0 1 6 0c0 2-3 2-3 4m0 3h.01',
 lock:'M5 10h14v11H5V10Zm3 0V6a4 4 0 0 1 8 0v4',
 logout:'M10 3H3v18h7m4-14 5 5-5 5m-7-5h15',
 arrow:'M5 12h14m-6-6 6 6-6 6',
 menu:'M3 6h18M3 12h18M3 18h18',
 close:'m6 6 12 12M6 18 18 6',
 check:'m5 12 4 4 10-10',
 chevron:'m6 9 6 6 6-6',
 mail:'M3 5h18v14H3V5Zm0 0 9 8 9-8'
};
export default function Icon({name,size=20,...props}){
 return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}><path d={paths[name]||paths.box}/></svg>;
}


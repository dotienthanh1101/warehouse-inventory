import {useEffect,useRef,useState} from 'react';

export default function GatewayVideoPreview({warehouseId,enabled,attempt}){
 const ref=useRef(null);
 const [message,setMessage]=useState('Đang kết nối video…'),[needsPlay,setNeedsPlay]=useState(false);
 useEffect(()=>{
  if(!enabled||!warehouseId)return;
  const video=ref.current;
  let alive=true,ws=null,media=null,buffer=null,url=null,timer=null,stats=null,callback=null;
  let retry=1000,queue=[],operation=null,opened=false,fatal=false,frames=0,previousFrames=0,lastSample=performance.now();
  let lastPacket=0;
  let startedPlayback=false,targetBuffer=.4,segmentSeconds=.2,videoSize='';
  function clearMedia(){
   if(url){URL.revokeObjectURL(url);url=null;}
   if(video){video.pause();video.removeAttribute('src');video.load();}
   media=null;buffer=null;queue=[];operation=null;opened=false;startedPlayback=false;
  }
  function pump(){
   if(!alive||!buffer||buffer.updating||operation||!queue.length)return;
   operation=queue.shift();
   try{buffer.appendBuffer(operation.data);}catch{ws?.close();}
  }
  function updated(){
   if(!alive||!buffer)return;
   if(operation?.sequence&&ws?.readyState===WebSocket.OPEN)ws.send(operation.sequence);
   operation=null;
   if(buffer.buffered.length){
    const end=buffer.buffered.end(buffer.buffered.length-1),start=buffer.buffered.start(buffer.buffered.length-1);
    if(!startedPlayback&&end-start>=targetBuffer){
     video.currentTime=Math.max(start,end-targetBuffer);startedPlayback=true;
    }
    if(startedPlayback){
     if(video.currentTime<start||end-video.currentTime>targetBuffer+2*segmentSeconds+.8)video.currentTime=Math.max(start,end-targetBuffer);
     const ahead=end-video.currentTime;
     video.playbackRate = ahead < .5 ? .75 : ahead < 1 ? .9 : ahead > targetBuffer + 1 ? 1.02 : 1;
     video.play().then(()=>{if(alive)setNeedsPlay(false);}).catch(()=>{if(alive)setNeedsPlay(true);});
    }
    if(end-buffer.buffered.start(0)>16&&!buffer.updating){
     operation={trim:true};buffer.remove(buffer.buffered.start(0),Math.min(end-10,Math.max(0,video.currentTime-2)));return;
    }
   }
   pump();
  }
  function connect(){
   if(!alive||fatal)return;
   clearMedia();setMessage('Đang kết nối video…');setNeedsPlay(false);
   const scheme=location.protocol==='https:'?'wss:':'ws:';
   ws=new WebSocket(`${scheme}//${location.host}/api/camera/gateway/video/view?warehouse_id=${encodeURIComponent(warehouseId)}`);
   ws.binaryType='arraybuffer';lastPacket=performance.now();
   ws.onmessage=event=>{
    if(!alive)return;
    if(typeof event.data==='string'){
     try{
      const meta=JSON.parse(event.data);
      if(meta.state==='waiting'){setMessage('Đã đăng nhập · chờ nguồn video tại kho…');return;}
      targetBuffer=Math.max(.2,Math.min(6,meta.buffer_seconds||.4));segmentSeconds=Math.max(.03,Math.min(5,meta.segment_seconds||.2));
      videoSize=`${meta.width}×${meta.height}`;
      const Media=window.MediaSource;
      if(!Media||!Media.isTypeSupported(meta.mime)){
       fatal=true;setMessage('Trình duyệt chưa hỗ trợ video H.264 này. Dùng Chrome hoặc Edge mới.');ws.close();return;
      }
      media=new Media();url=URL.createObjectURL(media);video.src=url;
      media.addEventListener('sourceopen',()=>{
       if(!alive||!media||media.readyState!=='open')return;
       try{buffer=media.addSourceBuffer(meta.mime);buffer.mode='segments';buffer.addEventListener('updateend',updated);buffer.addEventListener('error',()=>ws?.close());opened=true;pump();}
       catch{ws?.close();}
      },{once:true});
     }catch{ws.close();}
     return;
    }
    lastPacket=performance.now();retry=1000;
    const data=new Uint8Array(event.data);
    if(data[0]===73)queue.push({data:data.slice(1)});
    else if(data[0]===70&&data.length>17){
     const sequence=new DataView(event.data).getBigUint64(1).toString();
     queue.push({data:data.slice(17),sequence});
    }else{ws.close();return;}
    // Bounded appending window. Restart at a fresh keyframe rather than queue old video.
    if(queue.length>8){ws.close();return;}
    if(opened)pump();
   };
   ws.onerror=()=>ws?.close();
   ws.onclose=()=>{
    if(!alive||fatal)return;
    setMessage('Mất luồng video · đang kết nối lại…');clearMedia();
    timer=setTimeout(connect,retry);retry=Math.min(8000,retry*2);
   };
  }
  function countFrame(){if(!alive)return;frames++;callback=video.requestVideoFrameCallback(countFrame);}
  if(video.requestVideoFrameCallback)callback=video.requestVideoFrameCallback(countFrame);
  stats=setInterval(()=>{
   if(!alive||fatal)return;
   const now=performance.now();
   const count=video.requestVideoFrameCallback?frames:(video.getVideoPlaybackQuality?.().totalVideoFrames||0);
   const fps=Math.max(0,Math.round((count-previousFrames)*1000/(now-lastSample)));
   previousFrames=count;lastSample=now;
   if(ws?.readyState===WebSocket.OPEN){
    setMessage(fps>0?`${fps} FPS hiển thị · H.264 · ${videoSize}`:(startedPlayback?'Đang chờ video mới…':'Đang nạp bộ đệm video…'));
    if(now-lastPacket>15000)ws.close();
   }
  },1000);
  connect();
  return()=>{
   alive=false;clearTimeout(timer);clearInterval(stats);
   if(callback!==null&&video.cancelVideoFrameCallback)video.cancelVideoFrameCallback(callback);
   if(ws){ws.onclose=null;ws.onmessage=null;ws.close();}
   clearMedia();
  };
 },[warehouseId,enabled,attempt]);
 return <div style={{position:'relative',width:'100%',height:'100%',background:'#101d31'}}>
  <video ref={ref} muted playsInline aria-label="Video camera liên tục" style={{display:'block',width:'100%',height:'100%',objectFit:'contain'}}/>
  {needsPlay&&<button type="button" onClick={()=>ref.current?.play().then(()=>setNeedsPlay(false))} style={{position:'absolute',top:'45%',left:'35%'}}>Phát video</button>}
  <span role="status" style={{position:'absolute',bottom:8,left:8,padding:'5px 9px',borderRadius:6,background:'#17263cdd',color:'#fff',fontSize:12}}>{message}</span>
 </div>;
}

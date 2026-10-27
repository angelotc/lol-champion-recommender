/* Demo data: open index.html?demo (or ?demo=status, &scale=1.2, &mode=overlay, &tier=challenger). */
(function(){
  /* dev fallback */
  setTimeout(function(){
    if(window.pywebview||location.search.indexOf("demo")<0)return;
    var q=new URLSearchParams(location.search);
    if(q.get("scale"))window.setScale(q.get("scale"));
    if(q.get("mode"))window.setMode(q.get("mode"));
    var I=function(id){return"https://ddragon.leagueoflegends.com/cdn/16.21.1/img/champion/"+id+".png"};
    var C=function(n,id,k,l,d,g,w,t){return{kind:k,name:n,icon:I(id),lane:l,delta:d,games:g,weighted:w,tentative:!!t}};
    var rec=function(n,id,sc,base,cs){return{name:n,icon:I(id),score:sc,base:base,contributions:cs}};
    var view=q.get("demo")||"picks";
    if(view==="status"){window.updateState({view:"status",status:"waiting",message:"In queue - waiting for a match...",days:14,tier_id:"master_plus",tiers:[]});return}
    window.updateState({view:"picks",
      status:"active",role:"support",tier:"Master+",days:14,tier_id:q.get("tier")||"master_plus",note:q.get("tier")==="challenger"?"Few matchups at this tier: scores lean on overall win rate.":"",tiers:[{id:"master_plus",label:"Master+"},{id:"grandmaster_plus",label:"GM+"},{id:"challenger",label:"Challenger"}],
      allies:[{name:"Jinx",icon:I("Jinx"),lane:"bottom",tentative:false,is_me:false},
              {name:"Lee Sin",icon:I("LeeSin"),lane:"jungle",tentative:true,is_me:false},
              {name:"Me",icon:"",lane:"support",tentative:false,is_me:true}],
      enemies:[{id:111,name:"Nautilus",icon:I("Nautilus"),lane:"support",rates:{top:1,jungle:0,middle:0,bottom:0,support:98}},
               {id:51,name:"Caitlyn",icon:I("Caitlyn"),lane:"bottom",rates:{top:0,jungle:0,middle:1,bottom:98,support:0}},
               {id:910,name:"Hwei",icon:I("Hwei"),lane:"middle",flex:true,rates:{top:3,jungle:0,middle:66,bottom:24,support:8}}],
      enemy_damage:{ad:38,ap:62},
      bans:[{name:"Pyke",icon:I("Pyke")},{name:"Blitzcrank",icon:I("Blitzcrank")}],
      recs:[
        rec("Leona","Leona",5.57,1.99,[C("Nautilus","Nautilus","vs","support",4.8,431,2.4),C("Jinx","Jinx","with","bottom",2.2,310,1.1),C("Caitlyn","Caitlyn","vs","bottom",1.3,520,0.9),C("Lee Sin","LeeSin","with","jungle",0.6,140,0.2,true),C("Ahri","Ahri","vs","middle",-0.5,260,-0.2)]),
        rec("Braum","Braum",4.12,1.50,[C("Caitlyn","Caitlyn","vs","bottom",3.1,388,1.6),C("Jinx","Jinx","with","bottom",1.9,402,0.9),C("Nautilus","Nautilus","vs","support",0.8,300,0.3)]),
        rec("Rell","Rell",3.40,1.20,[C("Nautilus","Nautilus","vs","support",2.7,290,1.3),C("Jinx","Jinx","with","bottom",1.4,250,0.7)]),
        rec("Thresh","Thresh",2.85,1.45,[C("Jinx","Jinx","with","bottom",1.8,610,0.9),C("Ahri","Ahri","vs","middle",0.9,330,0.4)]),
        rec("Nami","Nami",1.92,0.80,[C("Jinx","Jinx","with","bottom",2.0,480,1.0),C("Nautilus","Nautilus","vs","support",-1.2,350,-0.6)]),
        rec("Rakan","Rakan",0.75,0.30,[C("Caitlyn","Caitlyn","vs","bottom",0.9,270,0.4),C("Nautilus","Nautilus","vs","support",-0.7,260,-0.3)]),
        rec("Milio","Milio",-0.40,0.60,[C("Nautilus","Nautilus","vs","support",-2.2,180,-0.9),C("Jinx","Jinx","with","bottom",1.1,200,0.5)]),
        rec("Lulu","Lulu",-1.35,0.10,[C("Nautilus","Nautilus","vs","support",-3.0,410,-1.5),C("Caitlyn","Caitlyn","vs","bottom",-1.4,360,-0.7)])
      ]});
  },500);
})();

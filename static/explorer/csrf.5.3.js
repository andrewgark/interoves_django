function p(e){var r=[],n="";for(n in e)r.push(n);return r}function f(e){return e=JSON.stringify(e),!(typeof e!="string"||!/^\{[\s\S]*\}$/.test(e))}function d(e){return e instanceof Array}function h(e){return Array.prototype.slice.call(e)}function u(){if(!(this instanceof u))return new u}u.prototype={get:function(e){for(var r=e+"=",n=document.cookie.split(";"),t=0;t<n.length;t++){for(var o=n[t];o.charAt(0)==" ";)o=o.substring(1,o.length);if(o.indexOf(r)==0)return decodeURI(o.substring(r.length,o.length))}return!1},set:function(e,r,n){if(f(e))for(const t in e)this.set(t,e[t],r,n);else if(typeof e=="string"){const t=f(n)?n:{expires:n},o=t.path!==void 0?`;path=${t.path};path=/`:";path=/",l=t.domain?`;domain=${t.domain}`:"",a=t.secure?";secure":"";let i=t.expires!==void 0?t.expires:"";typeof i=="string"&&i!==""?i=new Date(i):typeof i=="number"&&(i=new Date(+new Date+1e3*60*60*24*i)),i!==""&&"toGMTString"in i&&(i=`;expires=${i.toGMTString()}`);const g=t.sameSite?`;SameSite=${t.sameSite}`:"";document.cookie=`${e}=${encodeURI(r)+i+o+l+a+g}`}},remove:function(e){e=d(e)?e:h(arguments);for(var r=0,n=e.length;r<n;r++)this.set(e[r],"",-1);return e},clear:function(e){return e?this.remove(e):this.remove(p(this.all()))},all:function(){if(document.cookie==="")return{};for(var e=document.cookie.split("; "),r={},n=0,t=e.length;n<t;n++){var o=e[n].split("=");r[decodeURI(o[0])]=decodeURI(o[1])}return r}};let s=null;const c=function(e,r,n){const t=arguments;if(s||(s=u()),t.length===0)return s.all();if(t.length===1&&e===null)return s.clear();if(t.length===2&&!r)return s.clear(e);if(typeof e=="string"&&!r)return s.get(e);if(typeof e=="string"&&r||f(e))return s.set(e,r,n)};for(const e in u.prototype)c[e]=u.prototype[e];const y=document.getElementById("csrfCookieName").value,k=document.getElementById("csrfTokenInDOM").value==="True";function m(){if(k){let e=document.querySelector('input[name="csrfmiddlewaretoken"]');return e?e.value:null}return c.get(y)}export{c,m as g};
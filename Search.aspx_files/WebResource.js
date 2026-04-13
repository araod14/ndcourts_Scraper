var LBD_ImgId = null;
var LBD_Img = null;
var LBD_NewImg = null; 
var LBD_Parent = null;
var LBD_Prompt = null;

function LBD_LoadSound(soundPlaceholderId, soundLink)
{
    if(document.getElementById)
    {
        var i = soundLink.indexOf('&d=');
        if (-1 != i) {
          soundLink = soundLink.substring(0, i);
        }
        
        soundLink = soundLink + '&d=' + LBD_GetTimestamp();

        var placeholder = document.getElementById(soundPlaceholderId);
        var objectSrc = "<object id='captchaSound' classid='clsid:22D6F312-B0F6-11D0-94AB-0080C74C7E95' height='0' width='0' style='width:0; height:0;'><param name='AutoStart' value='1' /><param name='Volume' value='0' /><param name='PlayCount' value='1' /><param name='FileName' value='" + soundLink + "' /><embed id='captchaSoundEmbed' src='"+ soundLink + "' autoplay='true' hidden='true' volume='100' type='"+ LBD_GetMimeType() +"' style='display:inline;' /></object>";

        placeholder.innerHTML = "";
        placeholder.innerHTML = objectSrc;
    }
}

function LBD_GetTimestamp() {
  var d = new Date();
  var t = d.getTime() + (d.getTimezoneOffset() * 60000);
  return t;
}

function LBD_GetMimeType()
{
    var mimeType = "audio/x-wav";
    return mimeType;
}

var LBD_ImgId = null;
var LBD_Img = null;
var LBD_NewImg = null; 
var LBD_Parent = null;
var LBD_Prompt = null;

function LBD_ReloadImage(imgId)
{  
	if(imgId)
	{
		LBD_ImgId = imgId;
		LBD_Img = document.getElementById(LBD_ImgId);
		var src = LBD_Img.src;
		
		var i = src.indexOf('&d=');
        if (-1 != i) {
          src = src.substring(0, i);
        }
		var newSrc = src + '&d=' + LBD_GetTimestamp();

		LBD_NewImg = document.createElement('img');
		LBD_NewImg.onload = LBD_ShowImage;
		LBD_NewImg.id = LBD_Img.id;
		LBD_NewImg.alt = LBD_Img.alt;
		LBD_NewImg.src = newSrc;

		LBD_Prompt = document.createElement('span');
		LBD_Prompt.appendChild(document.createTextNode('loading...'));

		LBD_Parent = LBD_Img.parentNode;
		LBD_Parent.removeChild(LBD_Img);
		LBD_Parent.appendChild(LBD_Prompt);
	}
}

function LBD_ShowImage()
{
	if(LBD_NewImg && LBD_Parent && LBD_Prompt)
	{
		LBD_Parent.removeChild(LBD_Prompt);
		LBD_Parent.appendChild(LBD_NewImg);
	}
}

if ( (typeof(Sys) != "undefined") && (typeof(Sys.Application) != "undefined") ) {
  Sys.Application.notifyScriptLoaded();
}

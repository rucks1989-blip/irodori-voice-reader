using System;
using System.IO;
using System.Collections.Generic;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Drawing;
public static class SetupDialog {
    [STAThread] public static void Main(string[] args) {
        Application.EnableVisualStyles();
        var json=new JavaScriptSerializer();
        var request=json.Deserialize<Dictionary<string,string>>(File.ReadAllText(args[0],Encoding.UTF8));
        string value="cancel";
        if(request["kind"]=="progress") {
            using(var form=new Form()) {
                form.Text="Irodori Voice Reader セットアップ";form.ClientSize=new Size(510,145);form.StartPosition=FormStartPosition.CenterScreen;
                var label=new Label{Text="セットアップ中です。詳細はコンソールに表示されます。",Left=20,Top=20,Width=470,Height=40};
                var cancel=new Button{Text="安全に中止",Left=350,Top=85,Width=130};
                cancel.Click+=(s,e)=>{File.WriteAllText(args[0]+".cancel","cancel");cancel.Enabled=false;label.Text="今回起動した処理を終了しています。";};
                form.FormClosing+=(s,e)=>{if(!File.Exists(args[0]+".done")){File.WriteAllText(args[0]+".cancel","cancel");e.Cancel=true;}};
                var timer=new Timer{Interval=250};timer.Tick+=(s,e)=>{if(File.Exists(args[0]+".done"))form.Close();};timer.Start();
                form.Controls.AddRange(new Control[]{label,cancel});form.ShowDialog();timer.Dispose();
            }
        } else if(request["kind"]=="confirm") {
            value=MessageBox.Show(request["message"],"Irodori Voice Reader セットアップ",MessageBoxButtons.YesNo,MessageBoxIcon.Question,MessageBoxDefaultButton.Button2)==DialogResult.Yes?"yes":"no";
        } else if(request["kind"]=="file") {
            using(var form=new Form()) {
                form.Text="Irodori Voice Reader — ファイルを指定";form.ClientSize=new Size(720,205);form.StartPosition=FormStartPosition.CenterScreen;form.MaximizeBox=false;form.AllowDrop=true;
                var label=new Label {Text=request["message"]+"\r\nファイルの絶対パスを貼り付けるか、ファイルをこの画面へドラッグしてください。",Left=20,Top=15,Width=680,Height=60};
                var path=new TextBox {Left=20,Top=85,Width=680};
                var ok=new Button {Text="このファイルを使う",Left=380,Top=145,Width=150};
                var cancel=new Button {Text="指定せず進む",Left=550,Top=145,Width=150,DialogResult=DialogResult.Cancel};
                ok.Click+=(sender,e)=>{var candidate=path.Text.Trim().Trim('"');if(File.Exists(candidate)){value=candidate;form.DialogResult=DialogResult.OK;form.Close();}else MessageBox.Show("ファイルが見つかりません。パスを確認してください。");};
                form.DragEnter+=(sender,e)=>{if(e.Data.GetDataPresent(DataFormats.FileDrop))e.Effect=DragDropEffects.Copy;};
                form.DragDrop+=(sender,e)=>{var files=(string[])e.Data.GetData(DataFormats.FileDrop);if(files.Length>0)path.Text=files[0];};
                form.Controls.AddRange(new Control[]{label,path,ok,cancel});form.AcceptButton=ok;form.CancelButton=cancel;
                form.Shown+=(sender,e)=>path.Focus();form.ShowDialog();
            }
        } else {
            using(var form=new Form()) {
                form.Text="Irodori Voice Reader — ブラウザーを選択";form.ClientSize=new Size(510,180);form.StartPosition=FormStartPosition.CenterScreen;form.FormBorderStyle=FormBorderStyle.FixedDialog;form.MaximizeBox=false;
                var label=new Label {Text=request["message"],Left=20,Top=20,Width=470,Height=30};
                var choices=new ComboBox {Left=20,Top=65,Width=470,DropDownStyle=ComboBoxStyle.DropDownList};
                choices.Items.AddRange(request["kind"]=="backend" ? new object[]{"対応完成品を用意して再開","このPC向けにビルド（任意）","中止"} : new object[]{"Chromeのみ","Firefoxのみ","ChromeとFirefoxの両方"});choices.SelectedIndex=0;
                var ok=new Button {Text="次へ",Left=310,Top=125,DialogResult=DialogResult.OK};
                var cancel=new Button {Text="中止",Left=410,Top=125,DialogResult=DialogResult.Cancel};
                form.Controls.AddRange(new Control[]{label,choices,ok,cancel});form.AcceptButton=ok;form.CancelButton=cancel;
                if(form.ShowDialog()==DialogResult.OK)value=(request["kind"]=="backend" ? new[]{"obtain","build","cancel"} : new[]{"chrome","firefox","both"})[choices.SelectedIndex];
            }
        }
        File.WriteAllText(args[0]+".result",json.Serialize(new Dictionary<string,string>{{"value",value}}),new UTF8Encoding(false));
    }
}

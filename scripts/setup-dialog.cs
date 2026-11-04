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
        } else if(request["kind"]=="directory") {
            using(var form=new Form()) {
                form.Text="Irodori Voice Reader — 保存先を選択";
                form.ClientSize=new Size(720,210);form.StartPosition=FormStartPosition.CenterScreen;form.MaximizeBox=false;
                var label=new Label{Text=request["message"]+"\r\n「参照」からフォルダーを選択してください。中に専用フォルダーを作成します。",Left=20,Top=15,Width=680,Height=60};
                var path=new TextBox{Left=20,Top=85,Width=560,ReadOnly=true};
                var browse=new Button{Text="参照…",Left=590,Top=82,Width=110};
                var ok=new Button{Text="この保存先を使う",Left=380,Top=150,Width=150,Enabled=false};
                var cancel=new Button{Text="中止",Left=550,Top=150,Width=150,DialogResult=DialogResult.Cancel};
                browse.Click+=(sender,e)=>{
                    using(var picker=new FolderBrowserDialog()) {
                        picker.Description="Irodori Voice Readerの保存先フォルダーを選択";
                        picker.ShowNewFolderButton=true;
                        if(picker.ShowDialog(form)==DialogResult.OK){path.Text=picker.SelectedPath;ok.Enabled=true;}
                    }
                };
                ok.Click+=(sender,e)=>{value=path.Text;form.DialogResult=DialogResult.OK;form.Close();};
                form.Controls.AddRange(new Control[]{label,path,browse,ok,cancel});form.AcceptButton=ok;form.CancelButton=cancel;
                form.ShowDialog();
            }
        } else if(request["kind"]=="file") {
            using(var form=new Form()) {
                form.Text="Irodori Voice Reader — ファイルを指定";form.ClientSize=new Size(720,205);form.StartPosition=FormStartPosition.CenterScreen;form.MaximizeBox=false;form.AllowDrop=true;
                var label=new Label {Text=request["message"]+"\r\n「参照」からファイルを選択してください。",Left=20,Top=15,Width=680,Height=60};
                var path=new TextBox {Left=20,Top=85,Width=560,ReadOnly=true};
                var browse=new Button {Text="参照…",Left=590,Top=82,Width=110};
                browse.Click+=(sender,e)=>{
                    using(var picker=new OpenFileDialog()) {
                        picker.AutoUpgradeEnabled=false;
                        picker.Title=request["message"];
                        if(!String.IsNullOrEmpty(request["filter"]))picker.Filter=request["filter"];
                        if(picker.ShowDialog(form)==DialogResult.OK)path.Text=picker.FileName;
                    }
                };
                var ok=new Button {Text="このファイルを使う",Left=380,Top=145,Width=150};
                var cancel=new Button {Text="指定せず進む",Left=550,Top=145,Width=150,DialogResult=DialogResult.Cancel};
                ok.Click+=(sender,e)=>{var candidate=path.Text.Trim().Trim('"');if(File.Exists(candidate) || (request["kind"]=="directory" && (Directory.Exists(candidate) || Directory.Exists(Path.GetDirectoryName(candidate))))){value=candidate;form.DialogResult=DialogResult.OK;form.Close();}else MessageBox.Show("ファイルが見つかりません。パスを確認してください。");};
                form.DragEnter+=(sender,e)=>{if(e.Data.GetDataPresent(DataFormats.FileDrop))e.Effect=DragDropEffects.Copy;};
                form.DragDrop+=(sender,e)=>{var files=(string[])e.Data.GetData(DataFormats.FileDrop);if(files.Length>0)path.Text=files[0];};
                form.Controls.AddRange(new Control[]{label,path,browse,ok,cancel});form.AcceptButton=ok;form.CancelButton=cancel;
                form.Shown+=(sender,e)=>path.Focus();form.ShowDialog();
            }
        } else {
            using(var form=new Form()) {
                form.Text=request["kind"]=="installation"?"Irodori Voice Reader — 導入方法を選択":"Irodori Voice Reader — ブラウザーを選択";form.ClientSize=new Size(510,230);form.StartPosition=FormStartPosition.CenterScreen;form.FormBorderStyle=FormBorderStyle.FixedDialog;form.MaximizeBox=false;
                var label=new Label {Text=request["message"],Left=20,Top=20,Width=470,Height=80};
                var choices=new ComboBox {Left=20,Top=110,Width=470,DropDownStyle=ComboBoxStyle.DropDownList};
                choices.Items.AddRange(request["kind"]=="installation" ? new object[]{"前回の続きから再開","新しい別環境を作成","中止"} : request["kind"]=="stable" ? new object[]{"今回は導入しない（通常版のみ）","声質安定版の完成品を使用","声質安定版をビルド（任意）"} : request["kind"]=="backend" ? new object[]{"対応完成品を用意して再開","このPC向けにビルド（任意）","中止"} : new object[]{"Chromeのみ","Firefoxのみ","ChromeとFirefoxの両方"});choices.SelectedIndex=request["kind"]=="installation"?1:0;
                var ok=new Button {Text="次へ",Left=310,Top=175,DialogResult=DialogResult.OK};
                var cancel=new Button {Text="中止",Left=410,Top=175,DialogResult=DialogResult.Cancel};
                form.Controls.AddRange(new Control[]{label,choices,ok,cancel});form.AcceptButton=ok;form.CancelButton=cancel;
                if(form.ShowDialog()==DialogResult.OK)value=(request["kind"]=="installation" ? new[]{"resume","new","cancel"} : request["kind"]=="stable" ? new[]{"skip","prebuilt","build"} : request["kind"]=="backend" ? new[]{"obtain","build","cancel"} : new[]{"chrome","firefox","both"})[choices.SelectedIndex];
            }
        }
        File.WriteAllText(args[0]+".result",json.Serialize(new Dictionary<string,string>{{"value",value}}),new UTF8Encoding(false));
    }
}

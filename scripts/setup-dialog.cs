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
        string defaultValue=request.ContainsKey("default")?request["default"]:"";
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
        } else if(request["kind"]=="notice") {
            MessageBox.Show(request["message"],"Irodori Voice Reader セットアップ",MessageBoxButtons.OK,MessageBoxIcon.Warning);
            value="ok";
        } else if(request["kind"]=="directory") {
            using(var form=new Form()) {
                form.Text="Irodori Voice Reader — 保存先を選択";
                form.ClientSize=new Size(720,225);form.StartPosition=FormStartPosition.CenterScreen;form.MaximizeBox=false;
                var label=new Label{Text=request["message"]+"\r\n「参照」からフォルダーを選択してください。この場所に専用フォルダーを作成します。",Left=20,Top=15,Width=680,Height=60};
                var path=new TextBox{Name="installBasePath",Left=20,Top=85,Width=560,Text=defaultValue};
                var browse=new Button{Name="browseDirectory",Text="参照…",Left=590,Top=82,Width=110};
                var ok=new Button{Text="この保存先を使う",Left=380,Top=165,Width=150,Enabled=!String.IsNullOrWhiteSpace(defaultValue)};
                var cancel=new Button{Text="中止",Left=550,Top=165,Width=150,DialogResult=DialogResult.Cancel};
                path.TextChanged+=(sender,e)=>{ok.Enabled=!String.IsNullOrWhiteSpace(path.Text);};
                browse.Click+=(sender,e)=>{
                    using(var picker=new FolderBrowserDialog()) {
                        picker.Description="Irodori Voice Readerの保存先フォルダーを選択";
                        picker.ShowNewFolderButton=true;
                        if(Directory.Exists(path.Text.Trim()))picker.SelectedPath=path.Text.Trim();
                        if(picker.ShowDialog(form)==DialogResult.OK){path.Text=picker.SelectedPath;ok.Enabled=true;}
                    }
                };
                ok.Click+=(sender,e)=>{
                    var candidate=path.Text.Trim().Trim('"');
                    try {
                        if(!Path.IsPathRooted(candidate) || File.Exists(candidate))throw new ArgumentException();
                        candidate=Path.GetFullPath(candidate);
                    } catch {
                        MessageBox.Show("保存先のパスを確認してください。ドライブから始まるフォルダーを指定してください。");return;
                    }
                    value=candidate;form.DialogResult=DialogResult.OK;form.Close();
                };
                form.Controls.AddRange(new Control[]{label,path,browse,ok,cancel});form.AcceptButton=ok;form.CancelButton=cancel;
                form.Shown+=(sender,e)=>path.Focus();
                form.ShowDialog();
            }
        } else if(request["kind"]=="download") {
            using(var form=new Form()) {
                form.Text="Irodori Voice Reader — ダウンロードの復旧";
                form.ClientSize=new Size(820,620);form.StartPosition=FormStartPosition.CenterScreen;
                var help=new TextBox{Multiline=true,ReadOnly=true,ScrollBars=ScrollBars.Vertical,WordWrap=true,Left=20,Top=20,Width=780,Height=430,Text=request["message"],Anchor=AnchorStyles.Top|AnchorStyles.Left|AnchorStyles.Right|AnchorStyles.Bottom};
                var choices=new ComboBox{Name="downloadAction",Left=20,Top=470,Width=780,DropDownStyle=ComboBoxStyle.DropDownList,Anchor=AnchorStyles.Left|AnchorStyles.Right|AnchorStyles.Bottom};
                choices.Items.AddRange(new object[]{"1. 今すぐ再ダウンロード／再確認（何度でも）","2. 別の安全な取得方法で再試行","3. 配布元のダウンロードページを開く","4. 手動で取得した単体ファイル・ZIPを参照","5. 保存済み／ZIP展開済みフォルダーを参照","6. AIに相談する文を作る（自動送信なし）","7. 今回は中断して、後から再開"});
                choices.SelectedIndex=0;
                var ok=new Button{Name="runDownloadAction",Text="選んだ操作を実行",Left=490,Top=550,Width=160,Anchor=AnchorStyles.Right|AnchorStyles.Bottom};
                var cancel=new Button{Text="中断して後で再開",Left=660,Top=550,Width=140,DialogResult=DialogResult.Cancel,Anchor=AnchorStyles.Right|AnchorStyles.Bottom};
                ok.Click+=(sender,e)=>{value=new[]{"retry","alternative","page","file","folder","ai","cancel"}[choices.SelectedIndex];form.DialogResult=DialogResult.OK;form.Close();};
                form.Controls.AddRange(new Control[]{help,choices,ok,cancel});form.AcceptButton=ok;form.CancelButton=cancel;form.ShowDialog();
            }
        } else if(request["kind"]=="file" || request["kind"]=="folder") {
            using(var form=new Form()) {
                bool folder=request["kind"]=="folder";
                form.Text=folder?"Irodori Voice Reader — フォルダーを指定":"Irodori Voice Reader — ファイルを指定";form.ClientSize=new Size(720,205);form.StartPosition=FormStartPosition.CenterScreen;form.MaximizeBox=false;form.AllowDrop=true;
                var label=new Label {Text=request["message"]+"\r\n「参照」から保存場所を選択してください。移動やフォルダー作成は不要です。",Left=20,Top=15,Width=680,Height=60};
                var path=new TextBox {Name="selectedFilePath",Left=20,Top=85,Width=560,ReadOnly=true};
                var browse=new Button {Name="browseFile",Text="参照…",Left=590,Top=82,Width=110};
                browse.Click+=(sender,e)=>{
                    if(folder){
                        using(var picker=new FolderBrowserDialog()){
                            picker.Description=request["message"];picker.ShowNewFolderButton=false;
                            if(picker.ShowDialog(form)==DialogResult.OK)path.Text=picker.SelectedPath;
                        }
                        return;
                    }
                    using(var picker=new OpenFileDialog()) {
                        picker.AutoUpgradeEnabled=false;
                        picker.Title=request["message"];
                        if(!String.IsNullOrEmpty(request["filter"]))picker.Filter=request["filter"];
                        if(picker.ShowDialog(form)==DialogResult.OK)path.Text=picker.FileName;
                    }
                };
                var ok=new Button {Name="useSelectedFile",Text=folder?"このフォルダーを使う":"このファイルを使う",Left=380,Top=145,Width=150};
                var cancel=new Button {Text="指定せず進む",Left=550,Top=145,Width=150,DialogResult=DialogResult.Cancel};
                ok.Click+=(sender,e)=>{var candidate=path.Text.Trim().Trim('"');if(folder?Directory.Exists(candidate):File.Exists(candidate)){value=candidate;form.DialogResult=DialogResult.OK;form.Close();}else MessageBox.Show("指定した保存場所が見つかりません。参照から選び直してください。");};
                form.DragEnter+=(sender,e)=>{if(e.Data.GetDataPresent(DataFormats.FileDrop))e.Effect=DragDropEffects.Copy;};
                form.DragDrop+=(sender,e)=>{var files=(string[])e.Data.GetData(DataFormats.FileDrop);if(files.Length>0)path.Text=files[0];};
                form.Controls.AddRange(new Control[]{label,path,browse,ok,cancel});form.AcceptButton=ok;form.CancelButton=cancel;
                form.Shown+=(sender,e)=>path.Focus();form.ShowDialog();
            }
        } else {
            using(var form=new Form()) {
                form.Text=request["kind"]=="installation"?"Irodori Voice Reader — 導入方法を選択":"Irodori Voice Reader — ブラウザーを選択";form.ClientSize=new Size(510,230);form.StartPosition=FormStartPosition.CenterScreen;form.FormBorderStyle=FormBorderStyle.FixedDialog;form.MaximizeBox=false;
                var label=new Label {Text=request["message"],Left=20,Top=20,Width=470,Height=80};
                var choices=new ComboBox {Name="setupChoice",Left=20,Top=110,Width=470,DropDownStyle=ComboBoxStyle.DropDownList};
                choices.Items.AddRange(request["kind"]=="installation" ? new object[]{"前回の続きから再開","新しい別環境を作成","中止"} : request["kind"]=="stable" ? new object[]{"今回は導入しない（通常版のみ）","声質安定版の完成品を使用"} : request["kind"]=="backend" ? new object[]{"対応完成品を用意して再開","このPC向けにビルド（任意）","中止"} : new object[]{"Chromeのみ","Firefoxのみ","ChromeとFirefoxの両方"});choices.SelectedIndex=request["kind"]=="installation"?1:0;
                var ok=new Button {Name="continueSetup",Text="次へ",Left=310,Top=175,DialogResult=DialogResult.OK};
                var cancel=new Button {Text="中止",Left=410,Top=175,DialogResult=DialogResult.Cancel};
                form.Controls.AddRange(new Control[]{label,choices,ok,cancel});form.AcceptButton=ok;form.CancelButton=cancel;
                if(form.ShowDialog()==DialogResult.OK)value=(request["kind"]=="installation" ? new[]{"resume","new","cancel"} : request["kind"]=="stable" ? new[]{"skip","prebuilt"} : request["kind"]=="backend" ? new[]{"obtain","build","cancel"} : new[]{"chrome","firefox","both"})[choices.SelectedIndex];
            }
        }
        File.WriteAllText(args[0]+".result",json.Serialize(new Dictionary<string,string>{{"value",value}}),new UTF8Encoding(false));
    }
}

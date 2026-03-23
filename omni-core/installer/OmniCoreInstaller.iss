#define MyAppName "Omni-Core ERP"
        #define MyAppVersion "1.0.0"
        #define MyAppPublisher "Seu Nome/Empresa"
        #define MyAppExeName "Omni-Core-ERP-ObfDevManual.exe"
        #define MyAppId "{{045617C9-FC82-549D-8A2E-DEEB1D76F135}"

        [Setup]
        AppId={#MyAppId}
        AppName={#MyAppName}
        AppVersion={#MyAppVersion}
        AppVerName={#MyAppName} {#MyAppVersion}
        AppPublisher={#MyAppPublisher}
        DefaultDirName={autopf}\OmniCore
        DefaultGroupName=OmniCore
        DisableProgramGroupPage=no
        LicenseFile=C:\PROJETO IA\omni-core\LICENSE.txt
        OutputDir=C:\PROJETO IA\omni-core\releases
        OutputBaseFilename=OmniCore_Setup
        Compression=lzma
        SolidCompression=yes
        WizardStyle=modern
        PrivilegesRequired=admin
        ArchitecturesAllowed=x64compatible
        ArchitecturesInstallIn64BitMode=x64compatible
        UsePreviousAppDir=yes
        UninstallDisplayIcon={app}\{#MyAppExeName}


        [Languages]
        Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

        [Tasks]
        Name: "desktopicon"; Description: "Criar atalho na Area de Trabalho"; GroupDescription: "Atalhos adicionais:"; Flags: unchecked

        [Dirs]
        Name: "{userappdata}\OmniCore"
        Name: "{userappdata}\OmniCore\data"; Permissions: users-modify

        [Files]
        Source: "C:\PROJETO IA\omni-core\dist\Omni-Core-ERP-ObfDevManual.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\PROJETO IA\omni-core\theme.json"; DestDir: "{app}"; Flags: ignoreversion

        [Icons]
        Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
        Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
        Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"

        [Run]
        Filename: "{app}\{#MyAppExeName}"; Description: "Executar {#MyAppName} agora"; Flags: nowait postinstall skipifsilent

        [UninstallDelete]
        Type: filesandordirs; Name: "{userappdata}\OmniCore\data"; Check: ShouldDeleteAppData
        Type: dirifempty; Name: "{userappdata}\OmniCore"; Check: ShouldDeleteAppData

        [Code]
        var
          DeleteAppDataOnUninstall: Boolean;

        procedure InitializeWizard();
        begin
          WizardForm.WelcomeLabel2.Caption :=
            'Este assistente vai instalar o ' + ExpandConstant('{#MyAppName}') + ' em seu computador.' + #13#10 +
            'Os dados locais e a licenca continuarao protegidos pelo ecossistema Omni-Core.';
        end;

        function InitializeUninstall(): Boolean;
        begin
          DeleteAppDataOnUninstall := False;
          Result := True;
        end;

        procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
        var
          Answer: Integer;
        begin
          if CurUninstallStep = usUninstall then
          begin
            Answer := MsgBox(
              'Deseja apagar os dados de vendas e configuracoes?' + #13#10 +
              'Escolha "Nao" para manter a pasta em AppData e preservar o historico local.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2
            );
            DeleteAppDataOnUninstall := (Answer = IDYES);
          end;
        end;

        function ShouldDeleteAppData(): Boolean;
        begin
          Result := DeleteAppDataOnUninstall;
        end;

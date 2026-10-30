# Script fechado. Somente hardware parado; nenhuma operação inicia ou instala o guest.
# Sintaxe upstream: learn.microsoft.com/powershell/module/hyper-v/{new-vm,new-vhd,set-vm}.
# BEGIN_HOST_PREAMBLE: shared with native tests.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
# PS5.1 acrescenta Program Files ao PSModulePath mínimo; autoload varreria e poderia carregar
# módulos de terceiros. Cmdlets base vêm somente de $PSHOME; Hyper-V é importado explicitamente.
$PSModuleAutoLoadingPreference = 'None'
foreach ($module in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Management','Microsoft.PowerShell.Security')) {
    Import-Module -Name ([IO.Path]::Combine($PSHOME,'Modules',$module,($module+'.psd1'))) -ErrorAction Stop
}
# END_HOST_PREAMBLE
# BEGIN_READONLY_GUARDS: callable in tests without loading or invoking Hyper-V.
function Initialize-NativeGuard {
    # P/Invoke fechado em memória: sem compilador, DLL temporária ou TEMP herdado.
    $name = [Reflection.AssemblyName]::new('BeesHardwareNative')
    $assembly = [AppDomain]::CurrentDomain.DefineDynamicAssembly($name,[Reflection.Emit.AssemblyBuilderAccess]::Run)
    $module = $assembly.DefineDynamicModule('BeesHardwareNative')
    $type = $module.DefineType('BeesHardwareFile',[Reflection.TypeAttributes]::Public)
    $method = $type.DefinePInvokeMethod('GetFileInformationByHandle','kernel32.dll','GetFileInformationByHandle',([Reflection.MethodAttributes]::Public -bor [Reflection.MethodAttributes]::Static -bor [Reflection.MethodAttributes]::PinvokeImpl),[Reflection.CallingConventions]::Standard,[int],[Type[]]@([IntPtr],[IntPtr]),[Runtime.InteropServices.CallingConvention]::Winapi,[Runtime.InteropServices.CharSet]::None)
    $method.SetImplementationFlags([Reflection.MethodImplAttributes]::PreserveSig)
    $type.CreateType() | Out-Null
}
function Test-OwnedPath([string]$Candidate, [string]$OwnedRoot) {
    $full = [IO.Path]::GetFullPath($Candidate).TrimEnd('\')
    $base = [IO.Path]::GetFullPath($OwnedRoot).TrimEnd('\')
    return $full.Equals($base,[StringComparison]::OrdinalIgnoreCase) -or $full.StartsWith(($base+'\'),[StringComparison]::OrdinalIgnoreCase)
}
function Assert-HardwareNode([string]$Target, [string]$OwnedRoot, [bool]$Directory, [string]$VmId) {
    if (-not (Test-OwnedPath $Target $OwnedRoot)) { throw 'conflict' }
    $current = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $trusted = @($current,'S-1-5-18','S-1-5-32-544')
    if ($VmId) {
        $account = [Security.Principal.NTAccount]::new('NT VIRTUAL MACHINE', $VmId)
        $trusted += $account.Translate([Security.Principal.SecurityIdentifier]).Value
    }
    $cursor = [IO.Path]::GetFullPath($Target)
    $leaf = $true
    while ($true) {
        $item = Get-Item -LiteralPath $cursor -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'conflict' }
        $isDirectory = [bool]($item.Attributes -band [IO.FileAttributes]::Directory)
        if (($leaf -and $isDirectory -ne $Directory) -or (-not $leaf -and -not $isDirectory)) { throw 'conflict' }
        $acl = Get-Acl -LiteralPath $cursor
        $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
        if ($owner -notin @($current,'S-1-5-18','S-1-5-32-544')) { throw 'conflict' }
        foreach ($ace in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            if ($ace.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and -not ($ace.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly)) {
                # Arquivos da VM são privados: negar leitura e escrita por outros SIDs.
                if (([long]$ace.FileSystemRights -band 0x1F01FF) -and $ace.IdentityReference.Value -notin $trusted) { throw 'conflict' }
            }
        }
        if (-not $isDirectory) {
            $stream = [IO.File]::Open($cursor,[IO.FileMode]::Open,[IO.FileAccess]::Read,([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
            # BY_HANDLE_FILE_INFORMATION tem 52 bytes e nNumberOfLinks no offset 40.
            $info = [Runtime.InteropServices.Marshal]::AllocHGlobal(52)
            try {
                if (-not [BeesHardwareFile]::GetFileInformationByHandle($stream.SafeFileHandle.DangerousGetHandle(),$info) -or [Runtime.InteropServices.Marshal]::ReadInt32($info,40) -ne 1) { throw 'conflict' }
            } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($info); $stream.Dispose() }
        }
        if ([IO.Path]::GetFullPath($cursor).TrimEnd('\') -ieq [IO.Path]::GetFullPath($OwnedRoot).TrimEnd('\')) { break }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
        $leaf = $false
    }
}
# END_READONLY_GUARDS
try {
    $line = [Console]::In.ReadLine()
    if (-not $line -or $line.Length -gt 16384) { throw 'invalid' }
    $p = $line | ConvertFrom-Json
    $allowed = @('create_vhd','create_vm','configure_vm','remove_nic','attach_iso','verify','inspect')
    if ($p.operation -notin $allowed) { throw 'invalid' }
    $uuid = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    foreach ($name in @('installation_id','environment_id','plan_id','request_id')) {
        if ($p.$name -notmatch $uuid) { throw 'invalid' }
    }
    if ($p.vm_name -cne ('Bees-' + $p.installation_id + '-' + $p.environment_id)) { throw 'invalid' }
    if ($p.plan_hash -notmatch '^[0-9a-f]{64}$') { throw 'invalid' }
    if ($p.cpu_count -lt 1 -or $p.cpu_count -gt 4 -or $p.memory_bytes -lt 2GB -or $p.memory_bytes -gt 8GB -or $p.disk_bytes -lt 20GB -or $p.disk_bytes -gt 100GB) { throw 'invalid' }
    $root = [IO.Path]::GetFullPath($p.storage_root)
    if ($root.StartsWith('\\')) { throw 'invalid' }
    $owned = [IO.Path]::GetFullPath((Join-Path $root $p.plan_id))
    $disk = Join-Path $owned 'disk.vhdx'
    $iso = Join-Path $owned 'installer.iso'
    $marker = 'BeesManaged1:' + $p.plan_id + ':' + $p.plan_hash
    Import-Module Hyper-V -ErrorAction Stop
    $ready = @{ready=$true;pid=$PID;start_ticks=([Diagnostics.Process]::GetCurrentProcess().StartTime.ToUniversalTime().ToFileTimeUtc())}
    [Console]::Out.WriteLine(($ready | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
    $go = [Console]::In.ReadLine()
    if ($go -cne ('GO:' + $p.request_id)) { throw 'invalid' }
    Initialize-NativeGuard
    $vms = @(Get-VM -Name $p.vm_name -ErrorAction SilentlyContinue)
    if ($vms.Count -gt 1) { throw 'conflict' }
    $v = if ($vms.Count) { $vms[0] } else { $null }
    if ($p.operation -eq 'create_vhd') {
        if ($v -or (Test-Path -LiteralPath $owned)) { throw 'conflict' }
        New-Item -ItemType Directory -Path $owned | Out-Null
        # ACL da área de hardware é separada da credencial/journal CurrentUser-only.
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $acl = [Security.AccessControl.DirectorySecurity]::new()
        $acl.SetSecurityDescriptorSddlForm(('O:'+$sid+'D:P(A;OICI;FA;;;'+$sid+')(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)'))
        Set-Acl -LiteralPath $owned -AclObject $acl
        New-VHD -Path $disk -SizeBytes $p.disk_bytes -Fixed | Out-Null
    } elseif ($p.operation -eq 'create_vm') {
        if ($v -or -not (Test-Path -LiteralPath $disk)) { throw 'conflict' }
        Assert-HardwareNode $disk $owned $false ''
        $v = New-VM -Name $p.vm_name -Generation 2 -MemoryStartupBytes $p.memory_bytes -VHDPath $disk -Path $owned
        # Não existe -Id em New-VM. O UUID físico é observado após a criação.
        if ($v.State.ToString() -ne 'Off') { throw 'conflict' }
    } elseif ($p.operation -notin @('inspect','create_vhd')) {
        if (-not $v -or $v.State.ToString() -ne 'Off' -or $v.Generation -ne 2) { throw 'conflict' }
        if (-not $p.vm_id -or $p.vm_id -notmatch $uuid -or $v.Id.ToString() -cne $p.vm_id) { throw 'conflict' }
        if (-not (Test-OwnedPath $v.Path $owned)) { throw 'conflict' }
        $drives = @(Get-VMHardDiskDrive -VM $v)
        if ($drives.Count -ne 1 -or [IO.Path]::GetFullPath($drives[0].Path) -ine $disk) { throw 'conflict' }
        Assert-HardwareNode $disk $owned $false $p.vm_id
        if ($p.operation -eq 'configure_vm') {
            if ($v.Notes -and $v.Notes -cne $marker) { throw 'conflict' }
        } elseif ($v.Notes -cne $marker) { throw 'conflict' }
        if (@(Get-VMNetworkAdapter -VM $v | Where-Object {$_.SwitchId -and $_.SwitchId -ne [Guid]::Empty}).Count) { throw 'conflict' }
        switch ($p.operation) {
            'configure_vm' {
                Set-VM -VM $v -ProcessorCount $p.cpu_count -StaticMemory -MemoryStartupBytes $p.memory_bytes -AutomaticStartAction Nothing -AutomaticStopAction TurnOff -AutomaticCheckpointsEnabled $false -CheckpointType Disabled -Notes $marker
            }
            'remove_nic' { Get-VMNetworkAdapter -VM $v | Remove-VMNetworkAdapter -Confirm:$false }
            'attach_iso' {
                if (Test-Path -LiteralPath $iso) { throw 'conflict' }
                Copy-Item -LiteralPath $p.template_iso -Destination $iso
                Assert-HardwareNode $iso $owned $false $p.vm_id
                if ((Get-FileHash -LiteralPath $iso -Algorithm SHA256).Hash.ToLowerInvariant() -cne $p.image_iso_sha256) { throw 'conflict' }
                $dvd = @(Get-VMDvdDrive -VM $v)
                if ($dvd.Count -gt 0) { throw 'conflict' }
                Add-VMDvdDrive -VM $v -Path $iso | Out-Null
            }
        }
    }
    if ($v) { $v = Get-VM -Id $v.Id }
    $drives = if ($v) { @(Get-VMHardDiskDrive -VM $v) } else { @() }
    $adapters = if ($v) { @(Get-VMNetworkAdapter -VM $v) } else { @() }
    if (@($adapters | Where-Object {$_.SwitchId -and $_.SwitchId -ne [Guid]::Empty}).Count) { throw 'conflict' }
    $vhd = if (Test-Path -LiteralPath $disk) { Get-VHD -Path $disk } else { $null }
    if ($vhd) { Assert-HardwareNode $disk $owned $false $(if($v){$v.Id.ToString()}else{''}) }
    $dvd = if ($v) { @(Get-VMDvdDrive -VM $v) } else { @() }
    $isoAttached = [bool]($dvd.Count -eq 1 -and [IO.Path]::GetFullPath($dvd[0].Path) -ieq $iso)
    if ($isoAttached) {
        Assert-HardwareNode $iso $owned $false $v.Id.ToString()
        if ((Get-FileHash -LiteralPath $iso -Algorithm SHA256).Hash.ToLowerInvariant() -cne $p.image_iso_sha256) { throw 'conflict' }
    }
    $ownedVM = [bool]($v -and $drives.Count -eq 1 -and [IO.Path]::GetFullPath($drives[0].Path) -ieq $disk -and (Test-OwnedPath $v.Path $owned) -and (-not $v.Notes -or $v.Notes -ceq $marker) -and (-not $p.vm_id -or $v.Id.ToString() -ceq $p.vm_id))
    $result = @{
        found=[bool]($v -or $vhd);vm_id=$(if($v){$v.Id.ToString()}else{$null});owned=$(if($v){$ownedVM}else{[bool]$vhd});powered_off=[bool]($v -and $v.State.ToString() -eq 'Off');generation2=[bool]($v -and $v.Generation -eq 2);
        cpu_count=$(if($v){[int]$v.ProcessorCount}else{$null});memory_bytes=$(if($v){[long]$v.MemoryStartup}else{$null});disk_bytes=$(if($vhd){[long]$vhd.Size}else{$null});fixed_vhdx=[bool]($vhd -and $vhd.VhdFormat.ToString() -eq 'VHDX' -and $vhd.VhdType.ToString() -eq 'Fixed');network_adapter_count=$adapters.Count;iso_attached=$isoAttached
    }
    [Console]::Out.WriteLine(($result | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
    exit 0
} catch { exit 1 }

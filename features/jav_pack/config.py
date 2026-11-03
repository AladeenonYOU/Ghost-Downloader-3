from __future__ import annotations

from app.config.cfg import (
    BoolValidator,
    ConfigItem,
    OptionsConfigItem,
    OptionsValidator,
    RangeConfigItem,
    RangeValidator,
)
from app.models.pack import PackConfig

NAMING_MODES = ("code-title", "title", "code")
NAMING_LABELS = ("番号 + 标题", "仅标题", "仅番号")


class JavConfig(PackConfig):
    enabled = ConfigItem("Jav", "Enabled", True, BoolValidator())
    namingMode = OptionsConfigItem("Jav", "NamingMode", "code-title", OptionsValidator(list(NAMING_MODES)))
    saveByCodeFolder = ConfigItem("Jav", "SaveByCodeFolder", False, BoolValidator())
    uncensoredTag = ConfigItem("Jav", "UncensoredTag", "[无码破解]")
    useRelay = ConfigItem("Jav", "UseRelay", True, BoolValidator())
    relayPort = RangeConfigItem("Jav", "RelayPort", 18640, RangeValidator(1024, 65535))

    def settingGroups(self, parent: QWidget) -> list[CollapsibleSettingCardGroup]:
        from qfluentwidgets import ComboBoxSettingCard, FluentIcon, SwitchSettingCard
        from app.view.components.setting_card_group import CollapsibleSettingCardGroup
        from app.view.components.setting_cards import LineEditSettingCard

        javGroup = CollapsibleSettingCardGroup(self.tr("Jable / MissAV"), "jav", parent)
        enableCard = SwitchSettingCard(
            FluentIcon.GLOBE,
            self.tr("启用 Jable / MissAV 解析"),
            self.tr("粘贴视频详情页链接后自动解析播放清单，优先级高于通用 M3U8 解析"),
            self.enabled, javGroup,
        )
        namingCard = ComboBoxSettingCard(
            self.namingMode, FluentIcon.DOCUMENT,
            self.tr("文件命名"),
            self.tr("番号开头便于后续整理；无码片源会插入标记"),
            texts=[self.tr(label) for label in NAMING_LABELS], parent=javGroup,
        )
        folderCard = SwitchSettingCard(
            FluentIcon.FOLDER,
            self.tr("按番号建子文件夹"),
            self.tr("输出到「下载目录 / 番号 /」下，重下同一部片子不会与旧文件混在一起"),
            self.saveByCodeFolder, javGroup,
        )
        tagCard = LineEditSettingCard(
            FluentIcon.TAG, self.tr("无码片源标记"),
            self.tr("命中 MissAV 的无码片源时插入到番号之后，留空则不插入"),
            self.uncensoredTag, javGroup, placeholder="[无码破解]",
        )
        relayCard = SwitchSettingCard(
            FluentIcon.SYNC,
            self.tr("MissAV 走本地中转"),
            self.tr("surrit 会拒绝官方 N_m3u8DL-RE 的指纹，中转由 GD 自己取流后转发给下载器"),
            self.useRelay, javGroup,
        )

        javGroup.addSettingCards([enableCard, namingCard, folderCard, tagCard, relayCard])
        return [javGroup]


javConfig = JavConfig()

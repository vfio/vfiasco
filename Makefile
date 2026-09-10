NAME    := $(shell rpm -q --specfile vfiasco.spec --qf '%{NAME}\n' 2>/dev/null | head -1)
VERSION := $(shell rpm -q --specfile vfiasco.spec --qf '%{VERSION}\n' 2>/dev/null | head -1)

RPMBUILD := $(CURDIR)/.rpmbuild
TARBALL  := $(NAME)-$(VERSION).tar.gz

SOURCES := vfiasco.py vfiasco.spec README.md CHANGELOG.md \
           tests/ utils/ domain_xml/ config/ docs/

NEW_VERSION ?=

.PHONY: rpm srpm tarball clean release

rpm: tarball
	rpmbuild -ba vfiasco.spec \
		--define "_topdir $(RPMBUILD)" \
		--define "_sourcedir $(RPMBUILD)/SOURCES"
	@echo "---"
	@ls $(RPMBUILD)/RPMS/noarch/$(NAME)-*.rpm
	@ls $(RPMBUILD)/SRPMS/$(NAME)-*.src.rpm

srpm: tarball
	rpmbuild -bs vfiasco.spec \
		--define "_topdir $(RPMBUILD)" \
		--define "_sourcedir $(RPMBUILD)/SOURCES"
	@ls $(RPMBUILD)/SRPMS/$(NAME)-*.src.rpm

tarball: $(RPMBUILD)/SOURCES/$(TARBALL)

$(RPMBUILD)/SOURCES/$(TARBALL): $(shell find $(SOURCES) -type f 2>/dev/null) vfiasco.spec
	mkdir -p $(RPMBUILD)/SOURCES
	git archive --format=tar.gz --prefix=$(NAME)-$(VERSION)/ HEAD -o $@

release:
ifndef NEW_VERSION
	$(error Usage: make release NEW_VERSION=x.y.z)
endif
	@echo "Bumping $(VERSION) -> $(NEW_VERSION)"
	sed -i 's/^__version__ = ".*"/__version__ = "$(NEW_VERSION)"/' vfiasco.py
	sed -i 's/^# VFiasco .*/# VFiasco $(NEW_VERSION)/' README.md
	sed -i 's/^Version:        .*/Version:        $(NEW_VERSION)/' vfiasco.spec
	sed -i '/^%changelog/a * $(shell LC_ALL=C date "+%a %b %d %Y") $(shell git config user.name) <$(shell git config user.email)> - $(NEW_VERSION)-1\n- Update to $(NEW_VERSION)\n' vfiasco.spec
	sed -i '/^# Changelog/a \\n## [$(NEW_VERSION)] — $(shell date +%Y-%m-%d)' CHANGELOG.md
	@echo "---"
	@echo "Updated: vfiasco.py README.md vfiasco.spec CHANGELOG.md"
	@echo "Next: edit CHANGELOG.md, then commit"

clean:
	rm -rf $(RPMBUILD)
